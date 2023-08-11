from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .common import Piece


class UserOwnedObjectMixin:
    """
    UserOwnedObjectMixin

    Models must add these:
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(default=0)
    """

    def is_visible_to(self: "Piece", viewer):  # type: ignore
        owner = self.owner
        if owner == viewer:
            return True
        if not owner.is_active:
            return False
        if not viewer.is_authenticated:
            return self.visibility == 0
        if self.visibility == 2:
            return False
        if viewer.is_blocking(owner) or owner.is_blocking(viewer):
            return False
        if self.visibility == 1:
            return viewer.is_following(owner)
        else:
            return True

    def is_editable_by(self: "Piece", viewer):  # type: ignore
        return viewer.is_authenticated and (
            viewer.is_staff or viewer.is_superuser or viewer == self.owner
        )

    @classmethod
    def get_available(cls: "Type[Piece]", entity, request_user, following_only=False):  # type: ignore
        # e.g. SongMark.get_available(song, request.user)
        query_kwargs = {entity.__class__.__name__.lower(): entity}
        all_entities = cls.objects.filter(**query_kwargs).order_by(
            "-created_time"
        )  # get all marks for song
        visible_entities = list(
            filter(
                lambda _entity: _entity.is_visible_to(request_user)
                and (
                    _entity.owner.mastodon_acct in request_user.mastodon_following
                    if following_only
                    else True
                ),
                all_entities,
            )
        )
        return visible_entities
