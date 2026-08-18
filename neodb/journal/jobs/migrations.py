from django.db.models import OuterRef, Subquery
from loguru import logger

from catalog.models import Edition, item_content_types
from journal.models import Note, ShelfMember, ShelfMemberProgress, ShelfType


def backfill_member_progress_from_notes_20260720(batch_size: int = 1000) -> int:
    """Seed current reading progress from each book's latest progress note."""
    latest_progress_notes = (
        Note.objects.filter(
            owner_id=OuterRef("owner_id"),
            item_id=OuterRef("item_id"),
        )
        .exclude(progress_value__isnull=True)
        .exclude(progress_value="")
        .order_by("-created_time", "-pk")
    )
    members = (
        ShelfMember.objects.filter(
            parent__shelf_type=ShelfType.PROGRESS,
            item__polymorphic_ctype_id=item_content_types()[Edition],
            current_progress__isnull=True,
        )
        .annotate(
            latest_progress_type=Subquery(
                latest_progress_notes.values("progress_type")[:1]
            ),
            latest_progress_value=Subquery(
                latest_progress_notes.values("progress_value")[:1]
            ),
        )
        .exclude(latest_progress_value__isnull=True)
        .exclude(latest_progress_value="")
        .values("pk", "latest_progress_type", "latest_progress_value")
    )

    pending: list[ShelfMemberProgress] = []
    candidates = 0
    for member in members.iterator(chunk_size=batch_size):
        pending.append(
            ShelfMemberProgress(
                shelf_member_id=member["pk"],
                progress_type=member["latest_progress_type"],
                progress_value=member["latest_progress_value"],
            )
        )
        candidates += 1
        if len(pending) >= batch_size:
            ShelfMemberProgress.objects.bulk_create(
                pending,
                batch_size=batch_size,
                ignore_conflicts=True,
            )
            pending.clear()

    if pending:
        ShelfMemberProgress.objects.bulk_create(
            pending,
            batch_size=batch_size,
            ignore_conflicts=True,
        )

    logger.info(
        f"Backfilled current reading progress for up to {candidates} shelf members"
    )
    return candidates


def reindex_piece_keyed_docs_20260818(batch_size: int = 1000) -> int:
    """Rebuild the journal index for the piece-keyed doc id scheme.

    Piece docs were previously keyed by the latest linked post id; they
    are now keyed "p<pk>", so the new writers cannot address the old
    docs. Upsert every piece doc first, then delete the docs keyed under
    the old scheme, then write the piece-less post docs. Search stays
    populated throughout, and a failed delete leaves duplicates that
    idx-sync can collect rather than an empty index. Safe to run
    repeatedly. Holds two in-memory id sets, roughly a few dozen MB per
    million linked pieces.
    """
    from itertools import batched

    from django.core.paginator import Paginator

    from journal.models import Piece
    from journal.search import JournalIndex
    from takahe.models import Post

    index = JournalIndex.instance()
    if not index.initialize_collection(max_wait=30):
        logger.error("Journal index is not ready, reindex aborted.")
        return 0
    total = 0
    # old-scheme doc ids: one per piece with any linked post
    old_ids: set[str] = set()
    # live posts referenced by the docs written, to skip in the post pass
    covered_post_ids: set[int] = set()
    pieces = Piece.objects.order_by("id")
    pg = Paginator(pieces, batch_size)
    for p in pg.page_range:
        page = list(pg.get_page(p).object_list)
        docs = index.pieces_to_docs(page)
        for piece in page:
            if piece.latest_post_id:
                old_ids.add(str(piece.latest_post_id))
        for doc in docs:
            covered_post_ids.update(doc.get("post_id", []))
        total += index.replace_docs(docs)
    logger.info(f"Reindexed {total} journal piece docs")
    deleted = 0
    for chunk in batched(sorted(old_ids), 200):
        deleted += index.delete_docs("id", chunk)
    logger.info(f"Deleted {deleted} docs keyed under the old scheme")
    posts = Post.objects.filter(local=True).exclude(
        state__in=["deleted", "deleted_fanned_out"]
    )
    c = 0
    pg = Paginator(posts.order_by("id"), batch_size)
    for p in pg.page_range:
        docs = index.posts_to_docs(pg.get_page(p).object_list, covered_post_ids)
        c += index.replace_docs(docs)
    logger.info(f"Reindexed {c} journal post docs")
    return total + c
