from typing import TYPE_CHECKING

from users.models import APIdentity, User

if TYPE_CHECKING:
    from .common import Piece


class UserOwnedObjectMixin:
    """
    UserOwnedObjectMixin

    Models must add these:
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(default=0)
    """

    owner: APIdentity
    visibility: int

    def is_visible_to(self: "Piece | Self", viewing_user: User) -> bool:  # type: ignore
        owner = self.owner
        if not owner or not owner.is_active:
            return False
        if owner.user == viewing_user:
            return True
        if not viewing_user.is_authenticated:
            return self.visibility == 0
        viewer = viewing_user.identity  # type: ignore[assignment]
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

    def is_editable_by(self: "Piece", viewing_user: User):  # type: ignore
        return viewing_user.is_authenticated and (
            viewing_user.is_staff
            or viewing_user.is_superuser
            or viewing_user == self.owner.user
        )
