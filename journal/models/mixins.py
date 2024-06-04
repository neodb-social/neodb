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
        self: "Piece",  # type: ignore
        viewing_user: "User",
    ) -> bool:
        owner = self.owner
        if not owner or not owner.is_active:
            return False
        if owner.user == viewing_user:
            return True
        if not viewing_user.is_authenticated:
            return self.visibility == 0
        viewer = viewing_user.identity
        if not viewer:
            return False
        if self.visibility == 2:
            return False
        if viewer.is_blocking(owner) or owner.is_blocking(viewer):
            return False
        if self.visibility == 1:
            return viewer.is_following(owner)
        else:
            return True

    def is_editable_by(self: "Piece", viewing_user: "User"):  # type: ignore
        return viewing_user.is_authenticated and (
            viewing_user.is_staff
            or viewing_user.is_superuser
            or viewing_user == self.owner.user
        )
