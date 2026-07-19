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
