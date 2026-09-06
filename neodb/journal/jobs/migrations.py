import hashlib
import logging
import mimetypes
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery

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
from journal.models.attachment import (
    SOURCE_MAX_LENGTH,
    is_owned_upload,
    link_attachments_to_piece,
)
from users.models import APIdentity

logger = logging.getLogger(__name__)


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


def _backfill_bodies_20260818(model, field: str, migratable: bool = False) -> int:
    """Register and link the media embedded in every ``field`` of ``model``.

    Only pieces whose text actually contains a markdown image are scanned, and
    only local media is registered -- an external URL in a body is not ours.
    Files are adopted in place: the bytes already live where uploads belong,
    so nothing is copied.

    ``migratable`` says whether ``migrate_images`` can relocate this model's
    legacy paths. It only reads Review and Collection, so naming it for an
    Article would send an operator to a command that cannot help them.
    """
    pieces = model.objects.filter(**{f"{field}__contains": "!["}).select_related(
        "owner"
    )
    count = 0
    # Two unrelated reasons a path gets skipped, and only one is actionable.
    # The split is exactly whether the path sits under ``upload/`` at all,
    # because that is also what decides whether ``migrate_images`` can move it:
    # outside, it predates the layout and is migratable; inside but not the
    # owner's, it is a cross-owner hotlink (which ``normalize_image_src``
    # permits) or a malformed src, and in both cases migrate_images is a no-op.
    # Counting them together would warn a healthy site, every run, about
    # something needing no action.
    legacy = 0
    unattributable = 0
    for piece in pieces.iterator(chunk_size=200):
        text = getattr(piece, field) or ""
        try:
            # savepoint per piece, see _backfill_notes_20260818
            with transaction.atomic():
                resolved = Attachment.resolve_body_paths(text)
                link_attachments_to_piece(piece, text)
        except Exception as e:
            logger.warning(f"attachment backfill error on {piece}: {e}")
            continue
        for path in resolved:
            if is_owned_upload(path, piece.owner_id):
                continue
            if path.startswith("upload/"):
                unattributable += 1
            else:
                legacy += 1
        count += 1
    if legacy:
        # these stay outside the registry, and so unreclaimed on account
        # deletion, until their paths move
        remedy = (
            "run `neodb-manage migrate_images`, then re-run this backfill"
            if migratable
            else "migrate_images does not read this model, so these need moving "
            "by hand before they can be registered"
        )
        logger.warning(
            f"attachment backfill skipped {legacy} {model.__name__} image(s) "
            f"predating the upload/<identity_id>/ layout; {remedy}"
        )
    if unattributable:
        logger.info(
            f"attachment backfill left {unattributable} {model.__name__} image(s) "
            "unlinked: they belong to another user, or the src is malformed. "
            "Expected, no action needed -- claiming another user's upload would "
            "let one account's deletion break another's page"
        )
    return count


# --- legacy Note.attachments JSON -------------------------------------------
# The column is deprecated; these helpers exist only to bring its entries into
# the registry and go when it does.

# takahe's own key prefixes, from ``takahe.models.upload_namer``.
_TAKAHE_MEDIA_PREFIXES = ("attachments/", "attachment_thumbnails/")


def _bounded_source(prefix: str, value: str) -> str:
    """A ``source`` key for ``value`` that stays unique once bounded.

    Truncating the value to fit the column silently merges any two values
    sharing a prefix, so keep a readable head for debugging and let a digest
    of the whole value carry uniqueness.
    """
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:32]
    head = value[:200]
    return f"{prefix}:{head}:{digest}"[:SOURCE_MAX_LENGTH]


def takahe_media_path(url: str) -> str | None:
    """Storage-relative takahe path for ``url``, or ``None`` if not ours.

    Note attachment URLs recorded in the legacy JSON point at takahe's media.
    Resolving them back to a storage path lets the backfill copy the bytes for
    notes whose post takahe has since pruned -- the only source left for them.

    Works under both storage layouts, which differ more than they look:
    locally takahe has its own FileSystemStorage served under
    ``TAKAHE_MEDIA_URL``, while on S3 ``default`` and ``takahe`` are the *same*
    backend on one bucket with one base URL, so ``TAKAHE_MEDIA_URL`` does not
    appear in the URL at all. What identifies the file either way is takahe's
    own key prefix, so match on that after stripping whichever mount the URL
    came through.
    """
    if not url:
        return None
    if "://" in url:
        parsed = urlparse(url)
        # The URL on a federated post attachment is remote-controlled, and a
        # path-only match would let a crafted path claim an object in our own
        # bucket (worst when MEDIA_URL's path is "/", which makes any path
        # look local). Require the host to be ours before trusting the path.
        host = parsed.hostname or ""
        allowed = set(getattr(settings, "SITE_DOMAINS", [settings.SITE_DOMAIN]))
        for candidate in (
            settings.MEDIA_URL,
            settings.TAKAHE_MEDIA_URL,
            # takahe_attachment_urls absolutizes against site_url, which a
            # deployment may point at a host outside SITE_DOMAINS; without it
            # here the reader would reject what our own writer produced and
            # quietly downgrade every copy to a pointer row
            settings.SITE_INFO["site_url"],
        ):
            candidate_host = urlparse(candidate).hostname if candidate else ""
            if candidate_host:
                allowed.add(candidate_host)
        if host not in allowed:
            return None
        path = parsed.path
    else:
        path = url
    for prefix in (settings.TAKAHE_MEDIA_URL, settings.MEDIA_URL):
        prefix_path = urlparse(prefix).path if prefix and "://" in prefix else prefix
        if prefix_path and path.startswith(prefix_path):
            rel = path[len(prefix_path) :]
            if rel.startswith(_TAKAHE_MEDIA_PREFIXES):
                return rel
    return None


def register_legacy_attachment(
    owner: APIdentity, entry: dict[str, Any]
) -> Attachment | None:
    """Register a legacy ``Note.attachments`` JSON entry.

    The only path for a note whose post takahe has already pruned: the JSON
    URL is all that is left. A URL that resolves into takahe's own media store
    is copied; anything else (a proxy URL for remote media, or an off-site
    URL) becomes a pointer row.
    """
    url = (entry.get("url") or "").strip()
    if not url:
        return None
    mimetype = entry.get("mimetype") or ""
    rel_path = takahe_media_path(url)
    if rel_path:
        source = _bounded_source("takahe-media", rel_path)
        # a pointer left by an earlier failed copy is matched on the URL, so a
        # rerun upgrades it in place rather than rendering the media twice
        existing = (
            Attachment.objects.filter(owner=owner)
            .filter(Q(source=source) | Q(remote_url=url, file=""))
            .first()
        )
        if existing and existing.file:
            return existing
        ext_hint = mimetypes.guess_extension(mimetype) or ""
        copied = Attachment.copy_into_storage(
            owner.pk, storages["takahe"], rel_path, ext_hint
        )
        if copied and existing:
            existing.file = copied[0]
            existing.size = copied[1]
            existing.source = source
            existing.save(update_fields=["file", "size", "source"])
            return existing
        if copied:
            return Attachment.objects.create(
                owner=owner,
                file=copied[0],
                mimetype=mimetype,
                size=copied[1],
                source=source,
            )
        if existing:
            return existing
    return Attachment.pointer_for_url(
        owner, url, mimetype, entry.get("preview_url") or ""
    )


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

    The legacy JSON is deliberately not rewritten: it is deprecated and only
    this backfill reads it -- and rewriting it would mean saving Notes, which
    re-posts and re-indexes every one of them (``Piece.save`` ignores
    ``update_fields`` for its side effects). Notes that already hold rows are
    skipped, so re-running the backfill (migration 0019 runs it once,
    synchronously) only picks up notes it has not reached.
    """
    notes = (
        Note.objects.exclude(attachments=[])
        .filter(attachment_records__isnull=True)
        .select_related("owner")
    )
    count = 0
    for note in notes.iterator(chunk_size=200):
        try:
            # a savepoint per note: under the migration's transaction a
            # database error would otherwise abort every note after it (and
            # the migration would still be recorded as applied); under the
            # worker it is a plain transaction
            with transaction.atomic():
                post = note.latest_post
                registered = Attachment.sync_from_post(note, post) if post else []
                if not registered:
                    rows = []
                    for entry in note.attachments or []:
                        if not isinstance(entry, dict):
                            continue
                        a = register_legacy_attachment(note.owner, entry)
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
    # migrate_images reads Review and Collection only
    articles = _backfill_bodies_20260818(Article, "body")
    reviews = _backfill_bodies_20260818(Review, "body", migratable=True)
    collections = _backfill_bodies_20260818(Collection, "brief", migratable=True)
    notes = _backfill_notes_20260818()
    total = articles + reviews + collections + notes
    logger.info(
        "Backfilled attachments for "
        f"{articles} articles, {reviews} reviews, "
        f"{collections} collections, {notes} notes"
    )
    return total
