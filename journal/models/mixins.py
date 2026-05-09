from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import ForeignKey

    from users.models import APIdentity, User

    from .common import Piece


class UserOwnedObjectMixin:
    """
    UserOwnedObjectMixin

    Models must add these:
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(default=0)
    """

    if TYPE_CHECKING:
        owner: ForeignKey[Piece, APIdentity]
        # owner: ForeignKey[APIdentity, Piece]
        owner_id: int
        visibility: int

    def is_visible_to(
        self: "Piece",
        viewing_user: "User | None",
    ) -> bool:
        if viewing_user is None or not viewing_user.is_authenticated:
            return self.is_visible_to_identity(None)
        # Direct user-pk shortcut: an authenticated user always sees their own
        # content even when ``user.identity`` is unpopulated (rare: identity
        # deletion, mid-signup). Falling through to ``is_visible_to_identity``
        # with ``viewer = None`` would treat them as anonymous.
        owner = self.owner
        if owner and getattr(owner, "user_id", None) == viewing_user.pk:
            return True
        return self.is_visible_to_identity(viewing_user.identity)

    def is_visible_to_identity(
        self: "Piece",
        viewer: "APIdentity | None",
    ) -> bool:
        """Visibility check that takes an ``APIdentity`` directly.

        Used by signed AP endpoints where the caller is identified by HTTP
        signature rather than by a Django session — there is no ``User`` on
        the request, only the remote ``APIdentity`` resolved from the keyId.
        """
        owner = self.owner
        if not owner or not owner.is_active:
            return False
        if viewer is not None and viewer.pk == owner.pk:
            return True
        if viewer is None:
            return (
                self.visibility == 0
                and owner.anonymous_viewable
                and not owner.restricted
            )
        if self.visibility == 2:
            return False
        if viewer.is_blocking(owner) or owner.is_blocking(viewer):
            return False
        if owner.restricted and not viewer.is_following(owner):
            return False
        if self.visibility == 1:
            return viewer.is_following(owner)
        return True

    def is_editable_by(self: "Piece", viewing_user: "User"):
        return viewing_user.is_authenticated and (
            viewing_user.is_staff
            or viewing_user.is_superuser
            or viewing_user == self.owner.user
        )
