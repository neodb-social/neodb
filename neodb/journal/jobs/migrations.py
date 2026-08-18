from django.db.models import OuterRef, Subquery
from loguru import logger

from catalog.models import Edition, item_content_types
from journal.models import (
    Article,
    Attachment,
    Collection,
    Note,
    Review,
    ShelfMember,
    ShelfMemberProgress,
    ShelfType,
)
from journal.models.attachment import is_owned_upload, link_attachments_to_piece


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


def _backfill_bodies_20260818(model, field: str) -> int:
    """Register and link the media embedded in every ``field`` of ``model``.

    Only pieces whose text actually contains a markdown image are scanned, and
    only local media is registered -- an external URL in a body is not ours.
    Files are adopted in place: the bytes already live where uploads belong,
    so nothing is copied.
    """
    pieces = model.objects.filter(**{f"{field}__contains": "!["}).select_related(
        "owner"
    )
    count = 0
    skipped = 0
    for piece in pieces.iterator(chunk_size=200):
        text = getattr(piece, field) or ""
        try:
            resolved = Attachment.resolve_body_paths(text)
            link_attachments_to_piece(piece, text)
        except Exception as e:
            logger.warning(f"attachment backfill error on {piece}: {e}")
            continue
        # A local image outside the owner's ``upload/<id>/`` prefix cannot be
        # attributed safely, so it is skipped -- which on a deployment old
        # enough to predate that convention means those files stay out of the
        # registry, and unreclaimed on account deletion. Count them: silence
        # here would hide the one case where ``migrate_images`` still needs to
        # be run first.
        skipped += sum(1 for p in resolved if not is_owned_upload(p, piece.owner_id))
        count += 1
    if skipped:
        logger.warning(
            f"attachment backfill skipped {skipped} {model.__name__} image(s) "
            "outside the owner's upload/ prefix; run `neodb-manage migrate_images` "
            "to move legacy paths, then re-run this backfill"
        )
    return count


def _backfill_notes_20260818() -> int:
    """Register the attachments of every Note that has any.

    Two sources, in order of fidelity:

    1. the linked takahe post, which still holds the files plus their
       dimensions and alt text;
    2. the note's own ``attachments`` JSON, the only thing left once takahe
       has pruned the post.

    Media on a local post is copied into our storage (takahe prunes, and the
    copy is what keeps the note renderable); remote media is recorded as a
    pointer, never downloaded.

    The legacy JSON is deliberately not rewritten. ``Note.attachment_list``
    prefers the rows, so the column simply stops being read -- and rewriting
    it would mean saving Notes, which re-posts and re-indexes every one of
    them (``Piece.save`` ignores ``update_fields`` for its side effects).
    """
    notes = Note.objects.exclude(attachments=[]).select_related("owner")
    count = 0
    for note in notes.iterator(chunk_size=200):
        try:
            post = note.latest_post
            registered = Attachment.sync_from_post(note, post) if post else []
            if not registered:
                rows = []
                for entry in note.attachments or []:
                    if not isinstance(entry, dict):
                        continue
                    a = Attachment.from_legacy_json(note.owner, entry)
                    if a:
                        rows.append(a)
                if rows:
                    note.attachment_records.add(*rows)
                registered = rows
            if registered:
                count += 1
        except Exception as e:
            logger.warning(f"attachment backfill error on {note}: {e}")
    return count


def backfill_attachments_20260818() -> int:
    """Bring pre-existing user uploads into the attachment registry.

    Article / Review / Collection bodies are adopted in place; Note media is
    copied out of takahe. See the helpers for the per-source details.
    """
    articles = _backfill_bodies_20260818(Article, "body")
    reviews = _backfill_bodies_20260818(Review, "body")
    collections = _backfill_bodies_20260818(Collection, "brief")
    notes = _backfill_notes_20260818()
    total = articles + reviews + collections + notes
    logger.info(
        "Backfilled attachments for "
        f"{articles} articles, {reviews} reviews, "
        f"{collections} collections, {notes} notes"
    )
    return total
