from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import Item
from users.models import User

from .collection import Collection, CollectionMember, FeaturedCollection
from .comment import Comment
from .common import Content
from .itemlist import ListMember
from .rating import Rating
from .review import Review
from .shelf import Shelf, ShelfLogEntry, ShelfManager, ShelfMember
from .tag import Tag, TagManager, TagMember


def reset_journal_visibility_for_user(user: User, visibility: int):
    ShelfMember.objects.filter(owner=user).update(visibility=visibility)
    Comment.objects.filter(owner=user).update(visibility=visibility)
    Rating.objects.filter(owner=user).update(visibility=visibility)
    Review.objects.filter(owner=user).update(visibility=visibility)


def remove_data_by_user(user: User):
    ShelfMember.objects.filter(owner=user).delete()
    Comment.objects.filter(owner=user).delete()
    Rating.objects.filter(owner=user).delete()
    Review.objects.filter(owner=user).delete()
    TagMember.objects.filter(owner=user).delete()
    Tag.objects.filter(owner=user).delete()
    CollectionMember.objects.filter(owner=user).delete()
    Collection.objects.filter(owner=user).delete()
    FeaturedCollection.objects.filter(owner=user).delete()


def update_journal_for_merged_item(
    legacy_item_uuid: str, delete_duplicated: bool = False
):
    legacy_item = Item.get_by_url(legacy_item_uuid)
    if not legacy_item:
        logger.error("update_journal_for_merged_item: unable to find item")
        return
    new_item = legacy_item.merged_to_item
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        for p in cls.objects.filter(item=legacy_item):
            try:
                p.item = new_item
                p.save(update_fields=["item_id"])
            except:
                if delete_duplicated:
                    logger.warning(
                        f"deleted piece {p} when merging {cls.__name__}: {legacy_item} -> {new_item}"
                    )
                    p.delete()
                else:
                    logger.warning(
                        f"skip piece {p} when merging {cls.__name__}: {legacy_item} -> {new_item}"
                    )


def journal_exists_for_item(item: Item) -> bool:
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        if cls.objects.filter(item=item).exists():
            return True
    return False
