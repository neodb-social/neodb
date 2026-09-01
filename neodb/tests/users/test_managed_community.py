import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.utils import timezone

from common.durable_work import claim_due_dispatches, recover_expired_claims
from common.models import DurableDispatch
from mastodon.models import ManagedVinylHubCommunityAccount, MastodonAccount
from users.managed_community import (
    DISPATCH_PREFIX,
    ManagedCommunityAmbiguousError,
    PixelfedAccountEdgeClient,
    begin_managed_community_deletion,
    begin_managed_community_suspend,
    bootstrap_managed_identity,
    process_managed_community_dispatch,
    reconcile_managed_community_dispatches,
    reconcile_managed_community_observations,
    resume_managed_community,
)
from users.managed_identity import login_managed_identity
from users.models import ManagedCommunityProjection, User
from users.oneid import VerifiedManagedIdentity

ISSUER = "https://oneid.example.test/tenant"


def identity(subject="subject-123", **attributes):
    return VerifiedManagedIdentity(ISSUER, subject, attributes)


def active_result(token="community-secret"):
    return {
        "projection_exists": True,
        "external_subject": "subject-123",
        "user_id": 42,
        "profile_id": 43,
        "actor_uri": "https://community.example/@vhabcdef",
        "technical_handle": "vhabcdef",
        "lifecycle": "active",
        "credential": {
            "status": "active",
            "access_token": token,
            "scopes": ["read", "write", "follow"],
        },
    }


def move_to_observation(result, operation, state):
    result.projection.operation = operation
    result.projection.state = state
    result.projection.save(update_fields=["operation", "state", "updated_at"])
    dispatch = DurableDispatch.objects.get(
        responsibility_ref=f"{DISPATCH_PREFIX}{result.projection.pk}"
    )
    dispatch.state = DurableDispatch.State.OBSERVATION
    dispatch.next_attempt_at = None
    dispatch.save(update_fields=["state", "next_attempt_at", "updated_at"])
    return dispatch


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_first_auth_is_atomic_and_schedules_one_projection(monkeypatch, settings):
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)

    result = bootstrap_managed_identity(identity())
    assert result.user.username.startswith("vh")
    assert re.fullmatch(r"vh[a-z0-9]+", result.user.username)
    assert len(result.user.username) <= 30
    assert User.objects.count() == 1
    assert ManagedCommunityProjection.objects.count() == 1
    assert (
        DurableDispatch.objects.filter(
            responsibility_ref=f"{DISPATCH_PREFIX}{result.projection.pk}"
        ).count()
        == 1
    )

    repeated = bootstrap_managed_identity(identity(nickname="changed"))
    assert repeated.user.pk == result.user.pk
    assert ManagedCommunityProjection.objects.count() == 1
    assert User.objects.count() == 1


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_first_auth_rolls_back_user_when_projection_write_fails(monkeypatch):
    monkeypatch.setattr(
        "users.managed_community._ensure_dispatch",
        lambda _: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )
    with pytest.raises(RuntimeError):
        bootstrap_managed_identity(identity("rollback-subject"))
    assert User.objects.count() == 0
    assert not DurableDispatch.objects.exists()


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_concurrent_first_auth_converges_without_orphan(monkeypatch, settings):
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)

    def attempt(_):
        from django.db import close_old_connections

        close_old_connections()
        try:
            return bootstrap_managed_identity(identity("race-subject")).user.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        users = list(executor.map(attempt, range(2)))
    assert users[0] == users[1]
    assert User.objects.filter(username__startswith="vh").count() == 1
    assert ManagedCommunityProjection.objects.count() == 1


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_managed_role_coexists_but_is_not_profile_authority(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity())
    projection = result.projection
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "provision", lambda *a: active_result()
    )
    process_managed_community_dispatch(
        lease.dispatch_id, lease.lease_token, projection.pk
    )

    ordinary = MastodonAccount.objects.create(
        user=result.user,
        domain="ordinary.example",
        uid="ordinary-1",
        handle="ordinary@ordinary.example",
        account_data={"username": "ordinary"},
    )
    ordinary.access_token = "ordinary-secret"
    ordinary.save(update_fields=["access_data"])
    result.user.refresh_from_db()
    assert result.user.mastodon.pk == ordinary.pk
    assert ManagedVinylHubCommunityAccount.objects.filter(user=result.user).count() == 1
    managed = ManagedVinylHubCommunityAccount.objects.get(user=result.user)
    assert managed.access_token == "community-secret"
    assert "community-secret" not in str(managed.access_data)
    assert (
        projection.__class__.objects.get(pk=projection.pk).state
        == ManagedCommunityProjection.State.PROVISIONED
    )


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_ambiguous_provision_requires_read_repair_and_never_blind_repeats(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity())
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    calls = []

    def ambiguous(*args):
        calls.append("provision")
        raise ManagedCommunityAmbiguousError("timeout")

    monkeypatch.setattr(PixelfedAccountEdgeClient, "provision", ambiguous)
    process_managed_community_dispatch(
        lease.dispatch_id, lease.lease_token, result.projection.pk
    )
    result.projection.refresh_from_db()
    assert result.projection.state == ManagedCommunityProjection.State.UNKNOWN
    assert (
        DurableDispatch.objects.get(pk=lease.dispatch_id).state
        == DurableDispatch.State.OBSERVATION
    )

    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "read", lambda *a, **k: active_result()
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "renew", lambda *a, **k: active_result()
    )
    assert reconcile_managed_community_observations() == 1
    result.projection.refresh_from_db()
    assert result.projection.state == ManagedCommunityProjection.State.PROVISIONED
    assert calls == ["provision"]
    assert (
        DurableDispatch.objects.get(pk=lease.dispatch_id).state
        == DurableDispatch.State.RETIRED
    )


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_ambiguous_suspend_active_schedules_safe_retry_without_renew(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("suspend-active"))
    assert begin_managed_community_suspend(result.user)
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.SUSPEND,
        ManagedCommunityProjection.State.SUSPEND_UNKNOWN,
    )
    calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: calls.append("read") or {"lifecycle": "active"},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "renew",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("suspend observation must not renew")
        ),
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    assert calls == ["read"]
    assert dispatch.state == DurableDispatch.State.READY
    assert dispatch.last_outcome == DurableDispatch.Outcome.SAFE_RETRY
    assert result.projection.operation == ManagedCommunityProjection.Operation.SUSPEND
    assert result.projection.state == ManagedCommunityProjection.State.SUSPEND_UNKNOWN
    assert not result.user.is_active


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_ambiguous_resume_suspended_schedules_safe_retry_and_stays_blocked(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("resume-suspended"))
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.RESUME,
        ManagedCommunityProjection.State.UNKNOWN,
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: {"lifecycle": "suspended"},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "renew",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("suspended resume observation must not renew")
        ),
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.READY
    assert dispatch.last_outcome == DurableDispatch.Outcome.SAFE_RETRY
    assert result.projection.operation == ManagedCommunityProjection.Operation.RESUME
    assert result.projection.state == ManagedCommunityProjection.State.SUSPENDED
    assert not result.user.is_active


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_delete_active_observation_schedules_safe_retry_and_stays_blocked(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("delete-active"))
    assert begin_managed_community_deletion(result.user) is False
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.DELETE,
        ManagedCommunityProjection.State.DELETING,
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "delete_status",
        lambda *a, **k: {"lifecycle": "active"},
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.READY
    assert dispatch.last_outcome == DurableDispatch.Outcome.SAFE_RETRY
    assert result.projection.operation == ManagedCommunityProjection.Operation.DELETE
    assert result.projection.state == ManagedCommunityProjection.State.DELETE_UNKNOWN
    assert not result.user.is_active


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_delete_requested_observation_remains_observation(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("delete-requested"))
    assert begin_managed_community_deletion(result.user) is False
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.DELETE,
        ManagedCommunityProjection.State.DELETING,
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "delete_status",
        lambda *a, **k: {"lifecycle": "delete_requested"},
    )

    assert reconcile_managed_community_observations() == 0
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.OBSERVATION
    assert result.projection.operation == ManagedCommunityProjection.Operation.DELETE
    assert result.projection.state == ManagedCommunityProjection.State.DELETING
    assert not result.user.is_active


@pytest.mark.parametrize(
    "operation",
    [
        ManagedCommunityProjection.Operation.PROVISION,
        ManagedCommunityProjection.Operation.READ,
    ],
    ids=["provision", "read"],
)
@pytest.mark.django_db(databases="__all__", transaction=True)
def test_deleted_observation_safe_repairs_to_provision(
    monkeypatch, settings, operation
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity(f"deleted-{operation}"))
    dispatch = move_to_observation(
        result, operation, ManagedCommunityProjection.State.UNKNOWN
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: {"lifecycle": "deleted"},
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.READY
    assert dispatch.last_outcome == DurableDispatch.Outcome.SAFE_RETRY
    assert result.projection.operation == ManagedCommunityProjection.Operation.PROVISION
    assert result.projection.state == ManagedCommunityProjection.State.PENDING


@pytest.mark.parametrize("lifecycle", ["missing", "deleted"])
@pytest.mark.django_db(databases="__all__", transaction=True)
def test_suspend_absence_is_terminal_without_reprovision(
    monkeypatch, settings, lifecycle
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity(f"suspend-{lifecycle}"))
    account = ManagedVinylHubCommunityAccount.objects.create(
        user=result.user,
        domain="community.example",
        uid="community-user",
        handle="vhabcdef@community.example",
        account_data={"username": "vhabcdef"},
    )
    account.access_token = "revoked-token"
    account.save(update_fields=["access_data"])
    assert begin_managed_community_suspend(result.user)
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.SUSPEND,
        ManagedCommunityProjection.State.SUSPEND_UNKNOWN,
    )
    calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: {"lifecycle": lifecycle},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "provision",
        lambda *a, **k: (
            calls.append("provision")
            or (_ for _ in ()).throw(AssertionError("suspend must not reprovision"))
        ),
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    account.refresh_from_db()
    assert calls == []
    assert dispatch.state == DurableDispatch.State.RETIRED
    assert dispatch.last_outcome == DurableDispatch.Outcome.KNOWN_SUCCESS
    assert result.projection.operation == ManagedCommunityProjection.Operation.SUSPEND
    assert result.projection.state == ManagedCommunityProjection.State.SUSPENDED
    assert not result.user.is_active
    assert not account.access_token


@pytest.mark.parametrize("lifecycle", ["missing", "deleted"])
@pytest.mark.django_db(databases="__all__", transaction=True)
def test_resume_absence_reprovisions_and_reactivates_after_fresh_credential(
    monkeypatch, settings, lifecycle
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity(f"resume-{lifecycle}"))
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.RESUME,
        ManagedCommunityProjection.State.UNKNOWN,
    )
    calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: calls.append("read") or {"lifecycle": lifecycle},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "provision",
        lambda *a, **k: calls.append("provision") or active_result("fresh-token"),
    )

    assert reconcile_managed_community_observations() == 1
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    account = ManagedVinylHubCommunityAccount.objects.get(user=result.user)
    assert calls == ["read", "provision"]
    assert dispatch.state == DurableDispatch.State.RETIRED
    assert dispatch.last_outcome == DurableDispatch.Outcome.KNOWN_SUCCESS
    assert result.projection.operation == ManagedCommunityProjection.Operation.RESUME
    assert result.projection.state == ManagedCommunityProjection.State.PROVISIONED
    assert account.access_token == "fresh-token"
    assert result.user.is_active


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_resume_absence_repair_failure_stays_blocked(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("resume-repair-failure"))
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    dispatch = move_to_observation(
        result,
        ManagedCommunityProjection.Operation.RESUME,
        ManagedCommunityProjection.State.UNKNOWN,
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: {"lifecycle": "missing"},
    )

    def failed_provision(*args, **kwargs):
        raise ManagedCommunityAmbiguousError("repair unavailable")

    monkeypatch.setattr(PixelfedAccountEdgeClient, "provision", failed_provision)

    assert reconcile_managed_community_observations() == 0
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    result.user.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.OBSERVATION
    assert result.projection.operation == ManagedCommunityProjection.Operation.RESUME
    assert not result.user.is_active


@pytest.mark.parametrize(
    "operation",
    [
        ManagedCommunityProjection.Operation.SUSPEND,
        ManagedCommunityProjection.Operation.RESUME,
    ],
    ids=["suspend", "resume"],
)
@pytest.mark.django_db(databases="__all__", transaction=True)
def test_delete_requested_observation_preserves_lifecycle_intent(
    monkeypatch, settings, operation
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity(f"delete-requested-{operation}"))
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    dispatch = move_to_observation(
        result, operation, ManagedCommunityProjection.State.UNKNOWN
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: {"lifecycle": "delete_requested"},
    )

    assert reconcile_managed_community_observations() == 0
    dispatch.refresh_from_db()
    result.projection.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.OBSERVATION
    assert result.projection.operation == operation
    assert not result.user.is_active


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_session_uses_native_django_session_while_projection_pending(monkeypatch):
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity())
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.session.save()
    old_key = request.session.session_key
    login_managed_identity(request, result.identity)
    assert request.session.session_key != old_key
    request.session.save()
    readback = RequestFactory().get("/")
    readback.session = SessionStore(request.session.session_key)
    assert get_user(readback).pk == result.user.pk


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_suspend_invalidates_existing_product_session_and_stages_deletion(monkeypatch):
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity())
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.session.save()
    login_managed_identity(request, result.identity)
    request.session.save()

    assert begin_managed_community_suspend(result.user)
    result.user.refresh_from_db()
    assert not result.user.is_active
    readback = RequestFactory().get("/")
    readback.session = SessionStore(request.session.session_key)
    assert not get_user(readback).is_authenticated

    assert not begin_managed_community_deletion(result.user)
    projection = ManagedCommunityProjection.objects.get(pk=result.projection.pk)
    assert projection.state == ManagedCommunityProjection.State.DELETING
    assert projection.operation == ManagedCommunityProjection.Operation.DELETE


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_suspend_clears_revoked_local_credential_and_direct_resume_renews(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("resume-direct"))

    provision_lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "provision", lambda *a: active_result("old-token")
    )
    process_managed_community_dispatch(
        provision_lease.dispatch_id, provision_lease.lease_token, result.projection.pk
    )

    assert begin_managed_community_suspend(result.user)
    suspend_lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "revoke",
        lambda *a: calls.append("revoke") or {"credential": {"status": "revoked"}},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "suspend",
        lambda *a: calls.append("suspend") or {"lifecycle": "suspended"},
    )
    process_managed_community_dispatch(
        suspend_lease.dispatch_id, suspend_lease.lease_token, result.projection.pk
    )
    managed = ManagedVinylHubCommunityAccount.objects.get(user=result.user)
    assert not managed.access_token
    assert calls == ["revoke", "suspend"]

    assert resume_managed_community(result.user)
    resume_lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "resume",
        lambda *a: calls.append("resume") or {"lifecycle": "active"},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "renew",
        lambda *a: calls.append("renew") or active_result("new-token"),
    )
    result.user.refresh_from_db()
    assert not result.user.is_active
    process_managed_community_dispatch(
        resume_lease.dispatch_id, resume_lease.lease_token, result.projection.pk
    )
    managed.refresh_from_db()
    result.user.refresh_from_db()
    result.projection.refresh_from_db()
    assert managed.access_token == "new-token"
    assert managed.access_token != "old-token"
    assert "new-token" not in str(managed.access_data)
    assert result.user.is_active
    assert result.projection.state == ManagedCommunityProjection.State.PROVISIONED
    assert calls == ["revoke", "suspend", "resume", "renew"]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_direct_resume_renew_failure_keeps_product_blocked(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("resume-failure"))
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "resume", lambda *a: {"lifecycle": "active"}
    )

    def failed_renew(*args):
        raise ManagedCommunityAmbiguousError("renew unavailable")

    monkeypatch.setattr(PixelfedAccountEdgeClient, "renew", failed_renew)
    result.projection.operation = ManagedCommunityProjection.Operation.RESUME
    result.projection.state = ManagedCommunityProjection.State.UNKNOWN
    result.projection.save(update_fields=["operation", "state", "updated_at"])
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    process_managed_community_dispatch(
        lease.dispatch_id, lease.lease_token, result.projection.pk
    )
    result.user.refresh_from_db()
    result.projection.refresh_from_db()
    assert not result.user.is_active
    assert result.projection.state == ManagedCommunityProjection.State.UNKNOWN
    assert (
        DurableDispatch.objects.get(pk=lease.dispatch_id).state
        == DurableDispatch.State.OBSERVATION
    )


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_resume_observation_renews_stale_credential_before_reactivation(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("resume-observation"))
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    monkeypatch.setattr(
        PixelfedAccountEdgeClient, "provision", lambda *a: active_result("old-token")
    )
    process_managed_community_dispatch(
        lease.dispatch_id, lease.lease_token, result.projection.pk
    )

    managed = ManagedVinylHubCommunityAccount.objects.get(user=result.user)
    managed.access_token = "old-token"
    managed.save(update_fields=["access_data"])
    result.user.is_active = False
    result.user.save(update_fields=["is_active"])
    result.projection.operation = ManagedCommunityProjection.Operation.RESUME
    result.projection.state = ManagedCommunityProjection.State.UNKNOWN
    result.projection.save(update_fields=["operation", "state", "updated_at"])
    dispatch = DurableDispatch.objects.get(
        responsibility_ref=f"{DISPATCH_PREFIX}{result.projection.pk}"
    )
    dispatch.state = DurableDispatch.State.OBSERVATION
    dispatch.save(update_fields=["state", "updated_at"])

    calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *a, **k: calls.append("read") or {"lifecycle": "active"},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "renew",
        lambda *a, **k: calls.append("renew") or active_result("new-token"),
    )
    assert reconcile_managed_community_observations() == 1
    managed.refresh_from_db()
    result.user.refresh_from_db()
    result.projection.refresh_from_db()
    assert calls == ["read", "renew"]
    assert managed.access_token == "new-token"
    assert managed.access_token != "old-token"
    assert result.user.is_active
    assert result.projection.state == ManagedCommunityProjection.State.PROVISIONED


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_commit_before_enqueue_is_recovered_from_postgres(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("commit-before-enqueue"))
    calls = []

    class Queue:
        def enqueue(self, job, *args, **kwargs):
            calls.append((job, args, kwargs))

    monkeypatch.setattr(
        "common.durable_work.django_rq.get_queue", lambda *args, **kwargs: Queue()
    )
    reconciliation = reconcile_managed_community_dispatches()
    assert reconciliation.claimed == 1
    assert calls[0][0].__name__ == "process_managed_community_dispatch"
    assert calls[0][2]["job_id"].startswith("durable-dispatch-")
    assert (
        DurableDispatch.objects.get(
            responsibility_ref=f"{DISPATCH_PREFIX}{result.projection.pk}"
        ).state
        == DurableDispatch.State.CLAIMED
    )


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_redis_loss_keeps_postgres_responsibility_and_republishes(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("redis-loss"))

    def redis_down(*args, **kwargs):
        raise OSError("redis unavailable")

    monkeypatch.setattr("common.durable_work.django_rq.get_queue", redis_down)
    assert reconcile_managed_community_dispatches().enqueue_errors == 1
    dispatch = DurableDispatch.objects.get(
        responsibility_ref=f"{DISPATCH_PREFIX}{result.projection.pk}"
    )
    assert dispatch.last_outcome == DurableDispatch.Outcome.ENQUEUE_ERROR
    recover_expired_claims(
        now=timezone.now() + timedelta(minutes=6),
        responsibility_prefix=DISPATCH_PREFIX,
    )
    assert (
        DurableDispatch.objects.get(pk=dispatch.pk).state
        == DurableDispatch.State.OBSERVATION
    )

    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *args, **kwargs: {"lifecycle": "missing", "projection_exists": False},
    )
    assert reconcile_managed_community_observations() == 1
    assert (
        DurableDispatch.objects.get(pk=dispatch.pk).state == DurableDispatch.State.READY
    )
    published = []

    class Queue:
        def enqueue(self, job, *args, **kwargs):
            published.append((job, args, kwargs))

    monkeypatch.setattr(
        "common.durable_work.django_rq.get_queue", lambda *args, **kwargs: Queue()
    )
    assert reconcile_managed_community_dispatches().dispatched == 1
    assert published[0][0].__name__ == "process_managed_community_dispatch"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_worker_lease_expiry_requires_owner_observation(monkeypatch, settings):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("worker-restart"))
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    assert (
        recover_expired_claims(
            now=timezone.now() + timedelta(minutes=6),
            responsibility_prefix=DISPATCH_PREFIX,
        )
        == 1
    )
    dispatch = DurableDispatch.objects.get(pk=lease.dispatch_id)
    assert dispatch.state == DurableDispatch.State.OBSERVATION
    reads = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "read",
        lambda *args, **kwargs: reads.append(args) or {"lifecycle": "missing"},
    )
    assert reconcile_managed_community_observations() == 1
    assert reads
    assert (
        DurableDispatch.objects.get(pk=lease.dispatch_id).state
        == DurableDispatch.State.READY
    )
    assert result.projection.refresh_from_db() is None


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_delete_continues_without_browser_session_after_remote_terminal(
    monkeypatch, settings
):
    settings.DEBUG = True
    settings.PIXELFED_ACCOUNT_EDGE_URL = "http://community.example"
    settings.PIXELFED_ACCOUNT_EDGE_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr("users.managed_community._schedule_projection", lambda _: None)
    result = bootstrap_managed_identity(identity("delete-without-browser"))
    assert begin_managed_community_deletion(result.user) is False
    result.projection.refresh_from_db()
    assert result.projection.state == ManagedCommunityProjection.State.DELETING
    lease = claim_due_dispatches(responsibility_prefix=DISPATCH_PREFIX)[0]
    remote_calls = []
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "revoke",
        lambda *a: remote_calls.append("revoke") or {},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "delete",
        lambda *a: remote_calls.append("delete") or {},
    )
    monkeypatch.setattr(
        PixelfedAccountEdgeClient,
        "delete_status",
        lambda *a: remote_calls.append("delete-status") or {"lifecycle": "deleted"},
    )
    native_calls = []
    monkeypatch.setattr(
        "users.managed_community._complete_native_deletion",
        lambda user_id: native_calls.append(user_id),
    )
    process_managed_community_dispatch(
        lease.dispatch_id, lease.lease_token, result.projection.pk
    )
    assert remote_calls == ["revoke", "delete", "delete-status"]
    assert native_calls == [result.user.pk]
    result.projection.refresh_from_db()
    assert result.projection.state == ManagedCommunityProjection.State.DELETED
