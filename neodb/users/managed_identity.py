from dataclasses import dataclass

from django.contrib import auth
from django.db import IntegrityError, transaction
from django.http import HttpRequest

from .models import ManagedIdentityBinding, User
from .oneid import VerifiedManagedIdentity


class ManagedIdentityInvariantError(RuntimeError):
    """Raised when persisted identity data cannot be resolved unambiguously."""


class ManagedIdentityConflictError(ManagedIdentityInvariantError):
    """Raised when a verified identity is already owned by another user."""


@dataclass(frozen=True)
class ManagedIdentityResolution:
    identity: VerifiedManagedIdentity
    binding: ManagedIdentityBinding | None
    user: User | None

    @property
    def bootstrap_required(self) -> bool:
        return self.binding is None


def resolve_managed_identity(
    identity: VerifiedManagedIdentity,
) -> ManagedIdentityResolution:
    """Resolve the immutable identity anchor or return bootstrap-required.

    The database unique constraint is the authority for cardinality.  An
    ambiguous or orphaned row is an invariant failure, never a new-account
    signal.
    """

    try:
        binding = ManagedIdentityBinding.objects.get(
            issuer=identity.issuer,
            subject=identity.subject,
        )
    except ManagedIdentityBinding.DoesNotExist:
        return ManagedIdentityResolution(identity, None, None)
    except ManagedIdentityBinding.MultipleObjectsReturned as exc:
        raise ManagedIdentityInvariantError(
            "multiple bindings exist for one issuer and subject"
        ) from exc

    try:
        user = binding.user
    except User.DoesNotExist as exc:
        raise ManagedIdentityInvariantError(
            "managed identity binding is orphaned"
        ) from exc
    return ManagedIdentityResolution(identity, binding, user)


def bind_managed_identity(
    identity: VerifiedManagedIdentity, user: User
) -> ManagedIdentityBinding:
    """Create or converge a binding using database uniqueness authority.

    This primitive intentionally does not create users.  A caller such as #74
    may invoke it inside its own Product account transaction after creating a
    complete ``User``.
    """

    try:
        with transaction.atomic():
            binding, _ = ManagedIdentityBinding.objects.get_or_create(
                issuer=identity.issuer,
                subject=identity.subject,
                defaults={"user": user},
            )
    except IntegrityError:
        # A concurrent creator may win the unique constraint between the
        # lookup and insert.  Read the committed winner and apply the same
        # ownership check below; never silently reassign it.
        try:
            binding = ManagedIdentityBinding.objects.get(
                issuer=identity.issuer,
                subject=identity.subject,
            )
        except ManagedIdentityBinding.DoesNotExist as exc:
            raise ManagedIdentityInvariantError(
                "binding disappeared after a uniqueness race"
            ) from exc
        except ManagedIdentityBinding.MultipleObjectsReturned as exc:
            raise ManagedIdentityInvariantError(
                "multiple bindings exist for one issuer and subject"
            ) from exc

    if binding.user_id != user.pk:
        raise ManagedIdentityConflictError(
            "verified managed identity is already bound to another user"
        )
    return binding


def login_managed_identity(
    request: HttpRequest, identity: VerifiedManagedIdentity
) -> ManagedIdentityResolution:
    """Authenticate an already-bound identity through Django's session auth."""

    resolution = resolve_managed_identity(identity)
    if resolution.bootstrap_required:
        return resolution
    assert resolution.user is not None
    if not resolution.user.is_active:
        raise ManagedIdentityInvariantError("managed identity user is inactive")
    auth.login(request, resolution.user, backend="mastodon.auth.OAuth2Backend")
    return resolution


def logout_product_session(request: HttpRequest) -> None:
    """Invalidate the ordinary NeoDB/Django Product session."""

    auth.logout(request)
