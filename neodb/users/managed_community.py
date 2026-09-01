"""Product-owned bootstrap and lifecycle boundary for the Community edge."""

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.durable_work import (
    DispatchLease,
    claim_is_current,
    create_dispatch,
    enqueue_claimed_dispatch,
    mark_ambiguous,
    mark_safe_retry,
    mark_terminal,
    reconcile_due_dispatches,
    recover_expired_claims,
    schedule_safe_retry_after_observation,
)
from common.models import DurableDispatch
from mastodon.models import (
    ManagedVinylHubCommunityAccount,
)

from .managed_identity import resolve_managed_identity
from .models import ManagedCommunityProjection, ManagedIdentityBinding
from .oneid import VerifiedManagedIdentity

logger = logging.getLogger(__name__)

DISPATCH_PREFIX = "managed-community:"
_HANDLE_RE = re.compile(r"^vh[a-z0-9]+$")
_PATHS = {
    "provision": "/api/v1/internal/vinylhub/account-edge/provision",
    "read": "/api/v1/internal/vinylhub/account-edge/read",
    "renew": "/api/v1/internal/vinylhub/account-edge/credential/renew",
    "revoke": "/api/v1/internal/vinylhub/account-edge/credential/revoke",
    "suspend": "/api/v1/internal/vinylhub/account-edge/suspend",
    "resume": "/api/v1/internal/vinylhub/account-edge/resume",
    "delete": "/api/v1/internal/vinylhub/account-edge/delete",
    "delete_status": "/api/v1/internal/vinylhub/account-edge/delete-status",
}


class ManagedCommunityError(RuntimeError):
    pass


class ManagedCommunityConfigurationError(ManagedCommunityError):
    pass


class ManagedCommunityAmbiguousError(ManagedCommunityError):
    pass


class ManagedCommunityRejectedError(ManagedCommunityError):
    pass


class ManagedCommunityProtocolError(ManagedCommunityAmbiguousError):
    pass


class ManagedCommunityInvariantError(ManagedCommunityError):
    pass


class PixelfedAccountEdgeClient:
    """HTTP-only client for the exact Pixelfed #72 owner seam."""

    def __init__(self):
        self.base_url = str(getattr(settings, "PIXELFED_ACCOUNT_EDGE_URL", "")).rstrip(
            "/"
        )
        self.service_token = str(
            getattr(settings, "PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN", "")
        )
        if not self.base_url or not self.service_token:
            raise ManagedCommunityConfigurationError(
                "Pixelfed Account Edge URL and service token are required"
            )
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ManagedCommunityConfigurationError(
                "invalid Pixelfed Account Edge URL"
            )
        if not settings.DEBUG and parsed.scheme != "https":
            raise ManagedCommunityConfigurationError(
                "Pixelfed Account Edge URL must use HTTPS outside DEBUG"
            )
        self.timeout = float(getattr(settings, "PIXELFED_ACCOUNT_EDGE_TIMEOUT", 10.0))

    def _post(self, operation: str, payload: dict) -> dict:
        try:
            response = httpx.post(
                self.base_url + _PATHS[operation],
                json=payload,
                headers={"X-VinylHub-Service-Token": self.service_token},
                timeout=self.timeout,
            )
        except (httpx.HTTPError, OSError) as exc:
            raise ManagedCommunityAmbiguousError(
                "Pixelfed Account Edge request outcome is unknown"
            ) from exc
        if response.status_code in {400, 409, 422}:
            raise ManagedCommunityRejectedError(
                f"Pixelfed Account Edge rejected {operation}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ManagedCommunityAmbiguousError(
                f"Pixelfed Account Edge {operation} response is ambiguous"
            )
        try:
            result = response.json()
        except (ValueError, TypeError) as exc:
            raise ManagedCommunityProtocolError(
                "Pixelfed Account Edge returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise ManagedCommunityProtocolError(
                "Pixelfed Account Edge returned a non-object"
            )
        return result

    def provision(self, subject: str, handle: str, email: str, display_seed: str):
        return self._post(
            "provision",
            {
                "external_subject": subject,
                "technical_handle": handle,
                "technical_email": email,
                "display_seed": display_seed or None,
            },
        )

    def read(self, subject: str, repair: bool = True):
        return self._post("read", {"external_subject": subject, "repair": repair})

    def _subject(self, operation: str, subject: str):
        return self._post(operation, {"external_subject": subject})

    def renew(self, subject: str):
        return self._subject("renew", subject)

    def revoke(self, subject: str):
        return self._subject("revoke", subject)

    def suspend(self, subject: str):
        return self._subject("suspend", subject)

    def resume(self, subject: str):
        return self._subject("resume", subject)

    def delete(self, subject: str):
        return self._subject("delete", subject)

    def delete_status(self, subject: str):
        return self._subject("delete_status", subject)


@dataclass(frozen=True)
class ManagedIdentityResolution:
    identity: VerifiedManagedIdentity
    binding: ManagedIdentityBinding
    user: object
    projection: ManagedCommunityProjection


def _technical_email(identity: VerifiedManagedIdentity) -> str:
    digest = hashlib.sha256(
        f"{identity.issuer}\0{identity.subject}".encode()
    ).hexdigest()
    return f"vh{digest}@community.invalid"


def _display_seed(identity: VerifiedManagedIdentity) -> str:
    for key in ("nickname", "display_name"):
        value = identity.accepted_source_attributes.get(key)
        if isinstance(value, str):
            return value[:255]
    return ""


def _new_handle() -> str:
    return "vh" + secrets.token_hex(13)


def _active_dispatch(projection_id: int) -> DurableDispatch | None:
    return (
        DurableDispatch.objects.filter(
            responsibility_ref=f"{DISPATCH_PREFIX}{projection_id}"
        )
        .exclude(state=DurableDispatch.State.RETIRED)
        .order_by("id")
        .first()
    )


def _ensure_dispatch(projection: ManagedCommunityProjection) -> DurableDispatch:
    existing = _active_dispatch(projection.pk)
    if existing:
        return existing
    return create_dispatch(
        f"{DISPATCH_PREFIX}{projection.pk}", queue="mastodon", max_attempts=20
    )


def _schedule_projection(projection_id: int) -> None:
    try:
        reconcile_managed_community_dispatches(limit=1)
    except Exception:  # the durable row remains the recovery authority
        logger.exception(
            "managed Community dispatch scheduling failed",
            extra={"projection_id": projection_id},
        )


def _resolve(identity: VerifiedManagedIdentity) -> ManagedIdentityResolution:
    resolved = resolve_managed_identity(identity)
    if resolved.bootstrap_required or not resolved.binding or not resolved.user:
        raise ManagedCommunityInvariantError("managed identity is not bound")
    try:
        projection = ManagedCommunityProjection.objects.get(binding=resolved.binding)
    except ManagedCommunityProjection.DoesNotExist as exc:
        raise ManagedCommunityInvariantError(
            "managed Community projection is missing"
        ) from exc
    except ManagedCommunityProjection.MultipleObjectsReturned as exc:
        raise ManagedCommunityInvariantError(
            "multiple managed Community projections exist"
        ) from exc
    return ManagedIdentityResolution(
        identity, resolved.binding, resolved.user, projection
    )


def bootstrap_managed_identity(
    identity: VerifiedManagedIdentity,
) -> ManagedIdentityResolution:
    """Create the local account, binding, projection and dispatch atomically."""

    existing = resolve_managed_identity(identity)
    if not existing.bootstrap_required:
        return _ensure_existing(identity, existing.binding, existing.user)

    for _ in range(8):
        try:
            with transaction.atomic():
                binding = (
                    ManagedIdentityBinding.objects.filter(
                        issuer=identity.issuer, subject=identity.subject
                    )
                    .select_for_update()
                    .first()
                )
                if binding:
                    user = binding.user
                else:
                    handle = _new_handle()
                    user = get_user_model().register(username=handle)
                    binding = ManagedIdentityBinding.objects.create(
                        issuer=identity.issuer, subject=identity.subject, user=user
                    )
                projection = ManagedCommunityProjection.objects.filter(
                    binding=binding
                ).first()
                if projection is None:
                    projection = ManagedCommunityProjection.objects.create(
                        user=user,
                        binding=binding,
                        technical_handle=user.username,
                        technical_email=_technical_email(identity),
                        display_seed=_display_seed(identity),
                        state=ManagedCommunityProjection.State.PENDING,
                        operation=ManagedCommunityProjection.Operation.PROVISION,
                    )
                _ensure_dispatch(projection)
                projection_id = projection.pk
            transaction.on_commit(
                lambda projection_id=projection_id: _schedule_projection(projection_id)
            )
            return _resolve(identity)
        except IntegrityError:
            winner = resolve_managed_identity(identity)
            if not winner.bootstrap_required:
                return _ensure_existing(identity, winner.binding, winner.user)
    raise ManagedCommunityInvariantError("managed identity bootstrap did not converge")


def _ensure_existing(identity, binding, user) -> ManagedIdentityResolution:
    with transaction.atomic():
        projection = (
            ManagedCommunityProjection.objects.select_for_update()
            .filter(binding=binding)
            .first()
        )
        if projection is None:
            projection = ManagedCommunityProjection.objects.create(
                user=user,
                binding=binding,
                technical_handle=user.username,
                technical_email=_technical_email(identity),
                display_seed=_display_seed(identity),
                state=ManagedCommunityProjection.State.PENDING,
                operation=ManagedCommunityProjection.Operation.PROVISION,
            )
            _ensure_dispatch(projection)
        elif projection.state == ManagedCommunityProjection.State.UNKNOWN:
            projection.operation = ManagedCommunityProjection.Operation.READ
            projection.save(update_fields=["operation", "updated_at"])
            _ensure_dispatch(projection)
        elif projection.state == ManagedCommunityProjection.State.PENDING:
            _ensure_dispatch(projection)
        projection_id = projection.pk
    transaction.on_commit(lambda: _schedule_projection(projection_id))
    return _resolve(identity)


def _remote_subject(projection: ManagedCommunityProjection) -> str:
    return projection.binding.subject


def _set_error(projection, category: str, error: Exception) -> None:
    projection.last_error_category = category[:40]
    projection.last_error_text = str(error)[:500]
    projection.last_error_at = timezone.now()


def _managed_account(projection):
    accounts = list(
        ManagedVinylHubCommunityAccount.objects.filter(user_id=projection.user_id)
    )
    if len(accounts) > 1:
        raise ManagedCommunityInvariantError(
            "multiple managed Community accounts exist"
        )
    return accounts[0] if accounts else None


def _credential_from_result(result: dict) -> str:
    credential = result.get("credential")
    token = credential.get("access_token") if isinstance(credential, dict) else None
    scopes = credential.get("scopes") if isinstance(credential, dict) else None
    expected_scopes = {"read", "write", "follow"}
    if not isinstance(token, str) or not token:
        raise ManagedCommunityProtocolError("owner returned no credential secret")
    if (
        not isinstance(scopes, list)
        or not all(isinstance(scope, str) for scope in scopes)
        or set(scopes) != expected_scopes
    ):
        raise ManagedCommunityProtocolError(
            "owner returned an invalid managed Community credential scope"
        )
    if any(isinstance(scope, str) and scope.startswith("admin:") for scope in scopes):
        raise ManagedCommunityProtocolError(
            "owner returned an administrative managed Community credential"
        )
    return token


def _clear_managed_credential(projection) -> None:
    account = _managed_account(projection)
    if not account:
        return
    account.access_token = ""
    account.refresh_token = ""
    account.save(update_fields=["access_data", "modified"])


def _store_provision_result(projection, result: dict) -> bool:
    if result.get("lifecycle") != "active" or not result.get("projection_exists"):
        raise ManagedCommunityProtocolError(
            "provision did not return an active projection"
        )
    token = _credential_from_result(result)
    remote_id = str(result.get("user_id") or result.get("profile_id") or "")
    if not remote_id:
        raise ManagedCommunityProtocolError("provision returned no remote identity")
    account = _managed_account(projection)
    domain = urlsplit(PixelfedAccountEdgeClient().base_url).netloc
    handle = str(result.get("technical_handle") or projection.technical_handle)
    if "@" not in handle:
        handle = f"{handle}@{domain}"
    if account is None:
        account = ManagedVinylHubCommunityAccount.objects.create(
            user_id=projection.user_id,
            domain=domain,
            uid=remote_id,
            handle=handle,
            account_data={
                "id": remote_id,
                "username": handle.split("@", 1)[0],
                "url": str(result.get("actor_uri") or ""),
            },
        )
        # TypedModel proxy construction accepts only concrete DB fields.
        # Assign the inherited encrypted virtual field after insertion.
        account.access_token = token
        account.refresh_token = ""
        account.save(update_fields=["access_data"])
    else:
        account.access_token = token
        account.uid = remote_id
        account.handle = handle
        account.save(update_fields=["access_data", "uid", "handle", "modified"])
    projection.managed_account = account
    projection.remote_user_id = remote_id
    projection.remote_profile_url = str(result.get("actor_uri") or "")[:2048]
    projection.state = ManagedCommunityProjection.State.PROVISIONED
    projection.last_error_category = ""
    projection.last_error_text = ""
    projection.last_error_at = None
    return True


def _mark_projection_terminal(dispatch_id, lease_token, projection, outcome):
    projection.save(
        update_fields=[
            "managed_account",
            "remote_user_id",
            "remote_profile_url",
            "state",
            "last_error_category",
            "last_error_text",
            "last_error_at",
            "updated_at",
        ]
    )
    if lease_token == "observation":
        DurableDispatch.objects.filter(
            pk=dispatch_id, state=DurableDispatch.State.OBSERVATION
        ).update(
            state=DurableDispatch.State.RETIRED,
            next_attempt_at=None,
            last_outcome=outcome,
            updated_at=timezone.now(),
        )
    else:
        mark_terminal(dispatch_id, lease_token, outcome=outcome)


def _schedule_observation_safe_retry(
    dispatch_id, projection_id, *, state, operation, reason
):
    with transaction.atomic():
        projection = ManagedCommunityProjection.objects.select_for_update().get(
            pk=projection_id
        )
        projection.state = state
        projection.operation = operation
        projection.save(update_fields=["state", "operation", "updated_at"])
        schedule_safe_retry_after_observation(dispatch_id, reason=reason)


def _complete_native_deletion(user_id: int) -> None:
    from users.views.account import _complete_native_user_deletion

    _complete_native_user_deletion(get_user_model().objects.get(pk=user_id))


def _reprovision_after_resume_observation(
    dispatch_id, lease_token, projection_id, projection
):
    repaired = PixelfedAccountEdgeClient().provision(
        _remote_subject(projection),
        projection.technical_handle,
        projection.technical_email,
        projection.display_seed,
    )
    with transaction.atomic():
        projection = ManagedCommunityProjection.objects.select_for_update().get(
            pk=projection_id
        )
        _store_provision_result(projection, repaired)
        projection.user.is_active = True
        projection.user.save(update_fields=["is_active"])
        _mark_projection_terminal(
            dispatch_id,
            lease_token,
            projection,
            DurableDispatch.Outcome.KNOWN_SUCCESS,
        )


def process_managed_community_dispatch(
    dispatch_id: int, lease_token: str, projection_id: int
) -> None:
    if not claim_is_current(dispatch_id, lease_token):
        return
    projection = ManagedCommunityProjection.objects.select_related("binding").get(
        pk=projection_id
    )
    operation = projection.operation
    subject = _remote_subject(projection)
    client = PixelfedAccountEdgeClient()
    try:
        if operation == ManagedCommunityProjection.Operation.PROVISION:
            result = client.provision(
                subject,
                projection.technical_handle,
                projection.technical_email,
                projection.display_seed,
            )
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                _store_provision_result(projection, result)
                _mark_projection_terminal(
                    dispatch_id,
                    lease_token,
                    projection,
                    DurableDispatch.Outcome.KNOWN_SUCCESS,
                )
            return
        if operation == ManagedCommunityProjection.Operation.READ:
            _observe_projection(
                dispatch_id, lease_token, projection_id, client.read(subject)
            )
            return
        if operation == ManagedCommunityProjection.Operation.SUSPEND:
            client.revoke(subject)
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                _clear_managed_credential(projection)
            result = client.suspend(subject)
            if result.get("lifecycle") != "suspended":
                raise ManagedCommunityProtocolError("suspend did not return suspended")
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                projection.state = ManagedCommunityProjection.State.SUSPENDED
                _mark_projection_terminal(
                    dispatch_id,
                    lease_token,
                    projection,
                    DurableDispatch.Outcome.KNOWN_SUCCESS,
                )
            return
        if operation == ManagedCommunityProjection.Operation.RESUME:
            result = client.resume(subject)
            if result.get("lifecycle") != "active":
                raise ManagedCommunityProtocolError("resume did not return active")
            renewed = client.renew(subject)
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                _store_provision_result(projection, renewed)
                projection.user.is_active = True
                projection.user.save(update_fields=["is_active"])
                _mark_projection_terminal(
                    dispatch_id,
                    lease_token,
                    projection,
                    DurableDispatch.Outcome.KNOWN_SUCCESS,
                )
            return
        if operation == ManagedCommunityProjection.Operation.DELETE:
            client.revoke(subject)
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                _clear_managed_credential(projection)
            result = client.delete(subject)
            result = client.delete_status(subject)
            lifecycle = result.get("lifecycle")
            if lifecycle in {"deleted", "missing"}:
                with transaction.atomic():
                    projection = (
                        ManagedCommunityProjection.objects.select_for_update().get(
                            pk=projection_id
                        )
                    )
                    projection.state = ManagedCommunityProjection.State.DELETED
                    _mark_projection_terminal(
                        dispatch_id,
                        lease_token,
                        projection,
                        DurableDispatch.Outcome.KNOWN_SUCCESS,
                    )
                _complete_native_deletion(projection.user_id)
            else:
                raise ManagedCommunityAmbiguousError(
                    "remote deletion remains non-terminal"
                )
            return
        raise ManagedCommunityInvariantError(
            f"unsupported managed Community operation: {operation}"
        )
    except ManagedCommunityRejectedError as exc:
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            if operation == ManagedCommunityProjection.Operation.PROVISION:
                projection.state = ManagedCommunityProjection.State.REJECTED
            elif operation == ManagedCommunityProjection.Operation.SUSPEND:
                projection.state = ManagedCommunityProjection.State.SUSPEND_UNKNOWN
            elif operation == ManagedCommunityProjection.Operation.DELETE:
                projection.state = ManagedCommunityProjection.State.DELETE_UNKNOWN
            else:
                projection.state = ManagedCommunityProjection.State.UNKNOWN
            _set_error(projection, "owner_rejected", exc)
            projection.save(
                update_fields=[
                    "state",
                    "last_error_category",
                    "last_error_text",
                    "last_error_at",
                    "updated_at",
                ]
            )
            if operation in {
                ManagedCommunityProjection.Operation.SUSPEND,
                ManagedCommunityProjection.Operation.DELETE,
            }:
                mark_ambiguous(
                    dispatch_id,
                    lease_token,
                    error_category="owner_rejected",
                    error_text=str(exc),
                )
            else:
                mark_terminal(
                    dispatch_id,
                    lease_token,
                    outcome=DurableDispatch.Outcome.OWNER_REJECTED,
                )
    except ManagedCommunityError as exc:
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            projection.state = {
                ManagedCommunityProjection.Operation.SUSPEND: ManagedCommunityProjection.State.SUSPEND_UNKNOWN,
                ManagedCommunityProjection.Operation.DELETE: ManagedCommunityProjection.State.DELETE_UNKNOWN,
            }.get(operation, ManagedCommunityProjection.State.UNKNOWN)
            _set_error(projection, "ambiguous", exc)
            projection.save(
                update_fields=[
                    "state",
                    "last_error_category",
                    "last_error_text",
                    "last_error_at",
                    "updated_at",
                ]
            )
        mark_ambiguous(dispatch_id, lease_token, error_text=str(exc))


def _observe_projection(dispatch_id, lease_token, projection_id, result: dict):
    lifecycle = result.get("lifecycle")
    projection = ManagedCommunityProjection.objects.get(pk=projection_id)
    operation = projection.operation
    if lifecycle == "missing":
        if operation == ManagedCommunityProjection.Operation.DELETE:
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                projection.state = ManagedCommunityProjection.State.DELETED
                _mark_projection_terminal(
                    dispatch_id,
                    lease_token,
                    projection,
                    DurableDispatch.Outcome.KNOWN_SUCCESS,
                )
            _complete_native_deletion(projection.user_id)
            return
        if operation == ManagedCommunityProjection.Operation.SUSPEND:
            with transaction.atomic():
                projection = ManagedCommunityProjection.objects.select_for_update().get(
                    pk=projection_id
                )
                _clear_managed_credential(projection)
                projection.state = ManagedCommunityProjection.State.SUSPENDED
                _mark_projection_terminal(
                    dispatch_id,
                    lease_token,
                    projection,
                    DurableDispatch.Outcome.KNOWN_SUCCESS,
                )
            return
        if operation == ManagedCommunityProjection.Operation.RESUME:
            _reprovision_after_resume_observation(
                dispatch_id, lease_token, projection_id, projection
            )
            return
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            projection.state = ManagedCommunityProjection.State.PENDING
            projection.operation = ManagedCommunityProjection.Operation.PROVISION
            projection.save(update_fields=["state", "operation", "updated_at"])
        if lease_token == "observation":
            schedule_safe_retry_after_observation(
                dispatch_id, reason="owner proved no Community projection exists"
            )
        else:
            mark_safe_retry(
                dispatch_id,
                lease_token,
                error_category="owner_missing",
                error_text="Owner proved no Community projection exists.",
            )
        return
    if (
        lifecycle == "delete_requested"
        and operation == ManagedCommunityProjection.Operation.DELETE
    ):
        return
    if (
        lifecycle == "deleted"
        and operation == ManagedCommunityProjection.Operation.SUSPEND
    ):
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            _clear_managed_credential(projection)
            projection.state = ManagedCommunityProjection.State.SUSPENDED
            _mark_projection_terminal(
                dispatch_id,
                lease_token,
                projection,
                DurableDispatch.Outcome.KNOWN_SUCCESS,
            )
        return
    if (
        lifecycle == "deleted"
        and operation == ManagedCommunityProjection.Operation.RESUME
    ):
        _reprovision_after_resume_observation(
            dispatch_id, lease_token, projection_id, projection
        )
        return
    if (
        lifecycle == "deleted"
        and operation == ManagedCommunityProjection.Operation.DELETE
    ):
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            projection.state = ManagedCommunityProjection.State.DELETED
            _mark_projection_terminal(
                dispatch_id,
                lease_token,
                projection,
                DurableDispatch.Outcome.KNOWN_SUCCESS,
            )
        _complete_native_deletion(projection.user_id)
        return
    if lifecycle == "deleted":
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            projection.state = ManagedCommunityProjection.State.PENDING
            projection.operation = ManagedCommunityProjection.Operation.PROVISION
            projection.save(update_fields=["state", "operation", "updated_at"])
        if lease_token == "observation":
            schedule_safe_retry_after_observation(
                dispatch_id,
                reason="Owner proved the Community projection was deleted.",
            )
        else:
            mark_safe_retry(
                dispatch_id,
                lease_token,
                error_category="owner_deleted",
                error_text="Owner proved the Community projection was deleted.",
            )
        return
    if lifecycle == "suspended":
        if operation == ManagedCommunityProjection.Operation.RESUME:
            _schedule_observation_safe_retry(
                dispatch_id,
                projection_id,
                state=ManagedCommunityProjection.State.SUSPENDED,
                operation=operation,
                reason="Owner remains suspended; resume can be retried safely.",
            )
            return
        if operation == ManagedCommunityProjection.Operation.DELETE:
            _schedule_observation_safe_retry(
                dispatch_id,
                projection_id,
                state=ManagedCommunityProjection.State.DELETE_UNKNOWN,
                operation=operation,
                reason="Owner remains suspended; delete can be retried safely.",
            )
            return
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            projection.state = ManagedCommunityProjection.State.SUSPENDED
            _mark_projection_terminal(
                dispatch_id,
                lease_token,
                projection,
                DurableDispatch.Outcome.KNOWN_SUCCESS,
            )
        return
    if lifecycle == "active":
        if operation == ManagedCommunityProjection.Operation.SUSPEND:
            _schedule_observation_safe_retry(
                dispatch_id,
                projection_id,
                state=ManagedCommunityProjection.State.SUSPEND_UNKNOWN,
                operation=operation,
                reason="Owner remains active; suspend can be retried safely.",
            )
            return
        if operation == ManagedCommunityProjection.Operation.DELETE:
            _schedule_observation_safe_retry(
                dispatch_id,
                projection_id,
                state=ManagedCommunityProjection.State.DELETE_UNKNOWN,
                operation=operation,
                reason="Owner remains active; delete can be retried safely.",
            )
            return
        renewed = PixelfedAccountEdgeClient().renew(_remote_subject(projection))
        with transaction.atomic():
            projection = ManagedCommunityProjection.objects.select_for_update().get(
                pk=projection_id
            )
            _store_provision_result(projection, renewed)
            if projection.operation == ManagedCommunityProjection.Operation.RESUME:
                projection.user.is_active = True
                projection.user.save(update_fields=["is_active"])
            _mark_projection_terminal(
                dispatch_id,
                lease_token,
                projection,
                DurableDispatch.Outcome.KNOWN_SUCCESS,
            )
        return
    raise ManagedCommunityAmbiguousError("owner observation did not resolve lifecycle")


def _enqueue_managed_community_lease(lease: DispatchLease):
    projection_id = int(lease.responsibility_ref.removeprefix(DISPATCH_PREFIX))
    return enqueue_claimed_dispatch(
        lease, process_managed_community_dispatch, projection_id
    )


def reconcile_managed_community_dispatches(limit: int = 100):
    recover_expired_claims(limit=limit, responsibility_prefix=DISPATCH_PREFIX)
    return reconcile_due_dispatches(
        _enqueue_managed_community_lease,
        limit=limit,
        responsibility_prefix=DISPATCH_PREFIX,
    )


def reconcile_managed_community_observations(limit: int = 100) -> int:
    rows = list(
        DurableDispatch.objects.filter(
            state=DurableDispatch.State.OBSERVATION,
            responsibility_ref__startswith=DISPATCH_PREFIX,
        ).order_by("id")[:limit]
    )
    repaired = 0
    for dispatch in rows:
        try:
            projection_id = int(
                dispatch.responsibility_ref.removeprefix(DISPATCH_PREFIX)
            )
            projection = ManagedCommunityProjection.objects.select_related(
                "binding"
            ).get(pk=projection_id)
            client = PixelfedAccountEdgeClient()
            result = (
                client.delete_status(_remote_subject(projection))
                if projection.operation == ManagedCommunityProjection.Operation.DELETE
                else client.read(_remote_subject(projection), repair=True)
            )
            lifecycle = result.get("lifecycle")
            if lifecycle in {"active", "suspended", "deleted", "missing"}:
                _observe_projection(dispatch.pk, "observation", projection_id, result)
            else:
                continue
            repaired += 1
        except ManagedCommunityError:
            continue
    return repaired


def begin_managed_community_suspend(user) -> bool:
    projection = ManagedCommunityProjection.objects.filter(user=user).first()
    if not projection:
        return False
    with transaction.atomic():
        projection = ManagedCommunityProjection.objects.select_for_update().get(
            pk=projection.pk
        )
        user.is_active = False
        # Django's native session backend rejects sessions whose auth hash
        # changed, so this also invalidates existing Product sessions.
        user.set_password(secrets.token_urlsafe(32))
        user.save(update_fields=["is_active", "password"])
        projection.state = ManagedCommunityProjection.State.SUSPEND_UNKNOWN
        projection.operation = ManagedCommunityProjection.Operation.SUSPEND
        projection.save(update_fields=["state", "operation", "updated_at"])
        _ensure_dispatch(projection)
        projection_id = projection.pk
    transaction.on_commit(lambda: _schedule_projection(projection_id))
    return True


def resume_managed_community(user) -> bool:
    projection = ManagedCommunityProjection.objects.filter(user=user).first()
    if not projection:
        return False
    with transaction.atomic():
        projection = ManagedCommunityProjection.objects.select_for_update().get(
            pk=projection.pk
        )
        projection.state = ManagedCommunityProjection.State.UNKNOWN
        projection.operation = ManagedCommunityProjection.Operation.RESUME
        projection.save(update_fields=["state", "operation", "updated_at"])
        _ensure_dispatch(projection)
        projection_id = projection.pk
    transaction.on_commit(lambda: _schedule_projection(projection_id))
    return True


def begin_managed_community_deletion(user) -> bool:
    projection = ManagedCommunityProjection.objects.filter(user=user).first()
    if not projection or projection.state == ManagedCommunityProjection.State.DELETED:
        return True
    with transaction.atomic():
        projection = ManagedCommunityProjection.objects.select_for_update().get(
            pk=projection.pk
        )
        user.is_active = False
        user.set_password(secrets.token_urlsafe(32))
        user.save(update_fields=["is_active", "password"])
        projection.state = ManagedCommunityProjection.State.DELETING
        projection.operation = ManagedCommunityProjection.Operation.DELETE
        projection.save(update_fields=["state", "operation", "updated_at"])
        _ensure_dispatch(projection)
        projection_id = projection.pk
    transaction.on_commit(lambda: _schedule_projection(projection_id))
    return False
