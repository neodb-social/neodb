from auditlog.context import set_actor
from django.db import transaction
from django.db.utils import IntegrityError
from loguru import logger

from catalog.models import Item
from journal.search import JournalIndex
from users.models import APIdentity, User

from .article import Article
from .collection import Collection, CollectionMember, FeaturedCollection
from .comment import Comment
from .common import Content, Debris, Piece
from .itemlist import ListMember
from .note import Note
from .rating import Rating
from .review import Review
from .shelf import ShelfLogEntry, ShelfMember
from .tag import Tag, TagMember


def cleanup_deleted_post(post_pk: int) -> None:
    """Clean up journal pieces and index docs after a post is deleted.

    Both deletion paths must call this: the web UI post_delete view
    directly, and takahe's PostService.delete() via the
    takahe.ap_handlers.post_deleted queue callback.
    """
    for piece in Piece.objects.filter(posts__id=post_pk):
        if piece.local and piece.__class__ not in (Note, Article):
            # Marks/Reviews/Comments are NeoDB-managed; keep the piece even
            # if the user nukes the timeline post (legacy behavior), but
            # refresh its index doc, which may reference the deleted post
            piece.update_index()
            continue
        # delete piece if the deleted post is the most recent one for the piece
        if piece.latest_post_id == post_pk:
            logger.debug(f"Deleting piece {piece}")
            piece.delete_index()
            piece.delete()
        else:
            logger.debug(f"Matched piece {piece} has newer posts, not deleting")
    # Docs keyed by this post that nothing above rewrites (piece-less
    # posts, or posts orphaned by a shelf change that still link a
    # Comment/Rating indexing within its mark) must go now, or only
    # idx-sync can collect them. Piece docs rewritten above are safe:
    # the post is dead by this point, so those docs carry no post_id
    # field and this filter cannot match them.
    JournalIndex.instance().delete_by_post([post_pk])


def reset_journal_visibility_for_user(owner: APIdentity, visibility: int):
    ShelfMember.objects.filter(owner=owner).update(visibility=visibility)
    Comment.objects.filter(owner=owner).update(visibility=visibility)
    Rating.objects.filter(owner=owner).update(visibility=visibility)
    Review.objects.filter(owner=owner).update(visibility=visibility)


def remove_data_by_identity(owner: APIdentity):
    ShelfMember.objects.filter(owner=owner).delete()
    ShelfLogEntry.objects.filter(owner=owner).delete()
    Comment.objects.filter(owner=owner).delete()
    Rating.objects.filter(owner=owner).delete()
    Review.objects.filter(owner=owner).delete()
    TagMember.objects.filter(owner=owner).delete()
    Tag.objects.filter(owner=owner).delete()
    Note.objects.filter(owner=owner).delete()
    CollectionMember.objects.filter(owner=owner).delete()
    Collection.objects.filter(owner=owner).delete()
    FeaturedCollection.objects.filter(owner=owner).delete()
    Article.objects.filter(owner=owner).delete()
    index = JournalIndex.instance()
    index.delete_by_owner(owner.pk)
    logger.info(f"removed journal data by {owner}")


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
    for cls in (
        list(Content.__subclasses__())
        + list(ListMember.__subclasses__())
        + [ShelfLogEntry]
    ):
        for p in cls.objects.filter(item=legacy_item):
            with transaction.atomic():
                try:
                    p.item = new_item
                    p.save(update_fields=["item_id"])
                    if isinstance(p, (Content, ListMember)):
                        p.update_index()
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
        if isinstance(p, (Content, ListMember)):
            Debris.create_from_piece(p)
        p.delete()


def journal_exists_for_item(item: Item) -> bool:
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        if cls.objects.filter(item=item).exists():
            return True
    return False
