from auditlog.context import set_actor
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import Item
from users.models import APIdentity, User

from .collection import Collection, CollectionMember, FeaturedCollection
from .comment import Comment
from .common import Content, Debris
from .itemlist import ListMember
from .rating import Rating
from .review import Review
from .shelf import ShelfLogEntry, ShelfMember
from .tag import Tag, TagMember


def reset_journal_visibility_for_user(owner: APIdentity, visibility: int):
    ShelfMember.objects.filter(owner=owner).update(visibility=visibility)
    Comment.objects.filter(owner=owner).update(visibility=visibility)
    Rating.objects.filter(owner=owner).update(visibility=visibility)
    Review.objects.filter(owner=owner).update(visibility=visibility)


def remove_data_by_user(owner: APIdentity):
    ShelfMember.objects.filter(owner=owner).delete()
    ShelfLogEntry.objects.filter(owner=owner).delete()
    Comment.objects.filter(owner=owner).delete()
    Rating.objects.filter(owner=owner).delete()
    Review.objects.filter(owner=owner).delete()
    TagMember.objects.filter(owner=owner).delete()
    Tag.objects.filter(owner=owner).delete()
    CollectionMember.objects.filter(owner=owner).delete()
    Collection.objects.filter(owner=owner).delete()
    FeaturedCollection.objects.filter(owner=owner).delete()


def update_journal_for_merged_item_task(editing_user_id: int, legacy_item_uuid: str):
    with set_actor(User.objects.get(pk=editing_user_id)):
        update_journal_for_merged_item(legacy_item_uuid)


def update_journal_for_merged_item(
    legacy_item_uuid: str, delete_duplicated: bool = False
):
    legacy_item = Item.get_by_url(legacy_item_uuid)
    if not legacy_item:
        logger.error("update_journal_for_merged_item: unable to find item")
        return
    new_item = legacy_item.merged_to_item
    if not new_item:
        logger.error("update_journal_for_merged_item: unable to find merged_to_item")
        return
    delete_q = []
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        for p in cls.objects.filter(item=legacy_item):
            with transaction.atomic():
                try:
                    p.item = new_item
                    p.save(update_fields=["item_id"])
                except IntegrityError:
                    if delete_duplicated:
                        logger.warning(
                            f"deleted piece {p.pk} when merging {cls.__name__}: {legacy_item_uuid} -> {new_item.uuid}"
                        )
                        delete_q.append(p)
                    else:
                        logger.warning(
                            f"skip piece {p.pk} when merging {cls.__name__}: {legacy_item_uuid} -> {new_item.uuid}"
                        )
    for p in delete_q:
        Debris.create_from_piece(p)
        p.delete()


def journal_exists_for_item(item: Item) -> bool:
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        if cls.objects.filter(item=item).exists():
            return True
    return False
