"""Registry of user-uploaded files embedded in journal content.

Every upload that ends up inside a markdown body (Article / Review /
Collection brief) or attached to a Note gets a row here, so the site can
answer "which files does this user own", "which pieces use this file" and
"what can be reclaimed when an account is deleted" -- none of which is
answerable from a bare URL sitting in a markdown body.

Registration happens *before* the URL is embedded: the upload endpoints
(``journal.views.common.upload_image`` and the ``/api/me/attachment/``
API) create the row and hand back its URL, and saving the piece links the
row to it via :func:`link_attachments_to_piece`.

Note attachments arrive from the other direction -- they are uploaded to
takahe through the Mastodon API, not to us -- so :meth:`Attachment.sync_from_post`
copies media from *local* posts into our own storage (duplication is
intentional: takahe hard-prunes posts, and the copy is what keeps the note
renderable afterwards). Media on remote posts is never downloaded; it gets
a pointer row carrying the URLs takahe already serves it under, mirroring
how takahe's own ``PostAttachment`` represents media it has not cached.

The legacy ``Note.attachments`` JSON is still the fallback read path while
the async backfill runs; :attr:`Note.attachment_list` prefers rows and the
column can be dropped once every deployment's backfill has completed.
"""

import mimetypes
import os
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import Storage, default_storage, storages
from django.db import models
from django.utils import timezone
from loguru import logger

from users.models import APIdentity

from .renderers import RE_MD_IMAGE, normalize_image_src

if TYPE_CHECKING:
    from takahe.models import Post, PostAttachment

    from .common import Piece


def generate_attachment_path(identity_id: int | str, ext: str) -> str:
    """Storage-relative upload path: ``upload/<identity_id>/<year>/<uuid>.<ext>``

    Kept identical to the layout uploads have always used, so pre-existing
    files can be adopted by the registry in place without moving bytes.
    """
    year = timezone.now().strftime("%Y")
    return f"upload/{identity_id}/{year}/{uuid.uuid4()}.{ext.lstrip('.')}"


# takahe's own key prefixes, from ``takahe.models.upload_namer``.
_TAKAHE_MEDIA_PREFIXES = ("attachments/", "attachment_thumbnails/")

# ``source`` values are the dedupe key, and the column is varchar(500), so
# every one is truncated at construction -- a lookup built from an untruncated
# string could never match the row that was stored, which would make the
# dedupe silently fail and duplicate on every pass.
SOURCE_MAX_LENGTH = 500


def source_for_post_attachment(pk: int) -> str:
    """Settled ``source`` for a takahe ``PostAttachment``: copy done, or the
    media is remote and never will be copied."""
    return f"takahe:{pk}"[:SOURCE_MAX_LENGTH]


def pending_source_for_post_attachment(pk: int) -> str:
    """``source`` for local media whose copy failed and should be retried."""
    return f"takahe-pending:{pk}"[:SOURCE_MAX_LENGTH]


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
        media_host = urlparse(settings.MEDIA_URL).hostname or ""
        takahe_host = urlparse(settings.TAKAHE_MEDIA_URL).hostname or ""
        allowed = set(getattr(settings, "SITE_DOMAINS", [settings.SITE_DOMAIN]))
        if media_host:
            allowed.add(media_host)
        if takahe_host:
            allowed.add(takahe_host)
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


class Attachment(models.Model):
    """A file uploaded by a user, optionally embedded in one or more pieces."""

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    owner = models.ForeignKey(
        APIdentity, on_delete=models.CASCADE, related_name="attachments"
    )
    # An image pasted into two different reviews is one upload used twice, so
    # the link is many-to-many: an FK would silently drop the second use and
    # make "is this file still referenced" unanswerable. An empty set is a
    # meaningful state -- either uploaded but not yet embedded, or orphaned
    # by an edit that removed it.
    pieces = models.ManyToManyField(
        "journal.Piece", related_name="attachment_records", blank=True
    )
    # Nullable: media on a remote post is not downloaded, so such a row
    # carries only its remote URLs. unique so adopting an existing path twice
    # cannot create a duplicate row.
    file = models.FileField(
        upload_to="upload/", max_length=500, null=True, blank=True, unique=True
    )
    thumbnail = models.FileField(
        upload_to="upload/", max_length=500, null=True, blank=True
    )
    # Where to read an undownloaded attachment from. Both are needed because
    # note cards render the preview and the lightbox from different URLs.
    remote_url = models.CharField(max_length=500, blank=True, default="")
    remote_preview_url = models.CharField(max_length=500, blank=True, default="")
    mimetype = models.CharField(max_length=200, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # Playback length in seconds for audio/video uploads; None for images.
    duration = models.FloatField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    # Provenance of a copied file, e.g. ``takahe:1234``. Not unique -- a
    # re-run of the backfill duplicating a row is tolerable -- but indexed:
    # the ongoing Note sync runs on *every* post fetch and uses this to avoid
    # re-copying the same takahe media on each edit.
    source = models.CharField(max_length=500, blank=True, default="", db_index=True)
    created_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Rows are created in the order the media appears (post order for note
        # attachments), and note templates key their lightbox anchors off
        # forloop.counter, so a stable sequence is required rather than
        # cosmetic. Declared here, not as an order_by() at the read site, so a
        # prefetch_related is honoured instead of being defeated by a re-sort.
        ordering = ["created_time", "pk"]
        indexes = [
            models.Index(fields=["owner", "created_time"]),
        ]

    def __str__(self) -> str:
        return f"Attachment:{self.uid}:{self.file.name or self.remote_url}"

    @property
    def uuid(self) -> str:
        return self.uid.hex

    @property
    def url(self) -> str:
        """Full-size URL. Falls back to ``remote_url`` for undownloaded media."""
        if self.file:
            return self.file.url
        return self.remote_url

    @property
    def preview_url(self) -> str:
        """Preview URL, falling back to the full-size one.

        Note cards render the thumbnail from ``preview_url`` and the lightbox
        from ``url``; keeping both means switching them from the legacy JSON
        to these rows needs no template change.
        """
        if self.thumbnail:
            return self.thumbnail.url
        return self.remote_preview_url or self.url

    @property
    def type(self) -> str:
        """Media class -- ``image`` / ``video`` / ``audio`` / ``unknown``.

        Matches the ``type`` key of the legacy ``Note.attachments`` JSON so
        both shapes render through the same template branch.
        """
        return (self.mimetype or "unknown").split("/")[0]

    def to_json(self) -> dict[str, Any]:
        """Legacy ``Note.attachments`` JSON entry for this row."""
        return {
            "type": self.type,
            "mimetype": self.mimetype,
            "url": self.url,
            "preview_url": self.preview_url,
        }

    def delete_files(self) -> None:
        """Remove the stored blobs. Best-effort; missing files are ignored."""
        for f in (self.thumbnail, self.file):
            if not f:
                continue
            try:
                f.delete(save=False)
            except Exception as e:
                logger.warning(f"{self} file delete error {e}")

    # --- registration -----------------------------------------------------

    @classmethod
    def register(
        cls,
        owner: APIdentity,
        content: File,
        ext: str,
        mimetype: str = "",
        description: str = "",
        width: int | None = None,
        height: int | None = None,
        duration: float | None = None,
    ) -> "Attachment":
        """Store ``content`` and register it as an upload owned by ``owner``."""
        rel_path = generate_attachment_path(owner.pk, ext)
        saved_path = default_storage.save(rel_path, content)
        return cls.objects.create(
            owner=owner,
            file=saved_path,
            mimetype=mimetype or mimetypes.guess_type(saved_path)[0] or "",
            size=getattr(content, "size", 0) or 0,
            width=width,
            height=height,
            duration=duration,
            description=description,
        )

    @classmethod
    def adopt(cls, owner: APIdentity, rel_path: str) -> "Attachment | None":
        """Register an already-stored file in place, without copying bytes.

        Used to bring pre-existing body images into the registry. Returns
        ``None`` when the path holds no file, so a body referencing media
        that has since been deleted does not create a dangling row.
        """
        try:
            if not default_storage.exists(rel_path):
                return None
            size = default_storage.size(rel_path)
        except Exception as e:
            logger.warning(f"attachment adopt {rel_path} error {e}")
            return None
        attachment, _ = cls.objects.get_or_create(
            file=rel_path,
            defaults={
                "owner": owner,
                "mimetype": mimetypes.guess_type(rel_path)[0] or "",
                "size": size,
            },
        )
        return attachment

    # --- takahe media -----------------------------------------------------

    @classmethod
    def _copy_into_storage(
        cls, owner_id: int, storage: Storage, name: str, ext_hint: str = ""
    ) -> tuple[str, int] | None:
        """Copy a takahe file into our storage as ``(new_path, size)``.

        ``None`` when the source cannot be read. Always a real byte copy, not
        a rename: locally the two storages are different filesystem roots, and
        on S3 they share a bucket but the copy is the whole point -- takahe
        hard-prunes posts and takes its media with them.

        Streams rather than buffering: an audio or video attachment can be
        large and the backfill walks many notes in one worker.
        """
        if not name:
            return None
        ext = (os.path.splitext(name)[1] or ext_hint).lstrip(".") or "bin"
        dest = generate_attachment_path(owner_id, ext)
        try:
            size = storage.size(name)
        except Exception as e:
            logger.warning(f"attachment source {name} unreadable {e}")
            return None
        if not size:
            return None
        try:
            with storage.open(name, "rb") as src:
                saved = default_storage.save(
                    dest, File(src, name=os.path.basename(name))
                )
        except Exception as e:
            logger.warning(f"attachment copy {name} error {e}")
            return None
        return saved, size

    @classmethod
    def from_post_attachment(
        cls, owner: APIdentity, atta: "PostAttachment", copy_file: bool = True
    ) -> "Attachment | None":
        """Register one takahe ``PostAttachment``, copying it when local.

        Deduped on ``source``, because this runs on every post fetch: a note
        edited through the Mastodon API must not re-copy its images each time.
        With ``copy_file=False`` (a remote post) nothing is downloaded; the
        row records the URLs takahe already serves the media under.
        """
        source = source_for_post_attachment(atta.pk)
        pending = pending_source_for_post_attachment(atta.pk)
        existing = cls.objects.filter(owner=owner, source__in=[source, pending]).first()
        # A settled row is one we don't need to touch again: the copy landed,
        # or the media is remote and was never going to be copied.
        if existing and (existing.file or not copy_file):
            return existing
        mimetype = atta.mimetype or ""
        ext_hint = mimetypes.guess_extension(mimetype) or ""
        takahe_storage = storages["takahe"]
        copied = (
            cls._copy_into_storage(
                owner.pk, takahe_storage, atta.file.name or "", ext_hint
            )
            if copy_file and atta.file
            else None
        )
        copied_thumb = (
            cls._copy_into_storage(
                owner.pk, takahe_storage, atta.thumbnail.name or "", ext_hint
            )
            if copied and atta.thumbnail
            else None
        )
        if existing:
            # A retry of a previously failed local copy. Upgrade the pending
            # row in place rather than adding a second one.
            if not copied:
                return existing
            existing.file = copied[0]
            existing.thumbnail = copied_thumb[0] if copied_thumb else None
            existing.size = copied[1]
            existing.source = source
            existing.save(update_fields=["file", "thumbnail", "size", "source"])
            return existing
        fields: dict[str, Any] = {
            "owner": owner,
            "mimetype": mimetype,
            "width": atta.width,
            "height": atta.height,
            "description": atta.name or "",
        }
        if not copied:
            full = atta.full_url().absolute
            if not full:
                return None
            # A failed copy of *local* media is not a settled outcome -- takahe
            # hard-prunes posts, so silently giving up would forfeit the exact
            # guarantee this copy exists for. Mark it pending so the next sync
            # retries, rather than tagging it with the settled source and
            # having the dedupe check treat it as done forever.
            return cls.objects.create(
                remote_url=full[:500],
                remote_preview_url=(atta.thumbnail_url().absolute or "")[:500],
                source=pending if copy_file else source,
                **fields,
            )
        return cls.objects.create(
            file=copied[0],
            thumbnail=copied_thumb[0] if copied_thumb else None,
            size=copied[1],
            source=source,
            **fields,
        )

    @classmethod
    def from_legacy_json(
        cls, owner: APIdentity, entry: dict[str, Any]
    ) -> "Attachment | None":
        """Register a legacy ``Note.attachments`` JSON entry.

        The only path available for notes whose post takahe has already
        pruned: the JSON URL is all that is left. A URL that resolves into
        takahe's own media store is copied; anything else (a proxy URL for
        remote media, or an off-site URL) becomes a pointer row.
        """
        url = (entry.get("url") or "").strip()
        if not url:
            return None
        mimetype = entry.get("mimetype") or ""
        rel_path = takahe_media_path(url)
        if rel_path:
            # truncated once, then used for both the lookup and the row, so the
            # two cannot disagree
            source = f"takahe-media:{rel_path}"[:SOURCE_MAX_LENGTH]
            existing = cls.objects.filter(owner=owner, source=source).first()
            if existing:
                return existing
            ext_hint = mimetypes.guess_extension(mimetype) or ""
            copied = cls._copy_into_storage(
                owner.pk, storages["takahe"], rel_path, ext_hint
            )
            if copied:
                return cls.objects.create(
                    owner=owner,
                    file=copied[0],
                    mimetype=mimetype,
                    size=copied[1],
                    source=source,
                )
        source = f"url:{url}"[:SOURCE_MAX_LENGTH]
        existing = cls.objects.filter(owner=owner, source=source).first()
        if existing:
            return existing
        return cls.objects.create(
            owner=owner,
            remote_url=url[:500],
            remote_preview_url=(entry.get("preview_url") or "")[:500],
            mimetype=mimetype,
            source=source,
        )

    @classmethod
    def sync_from_post(cls, piece: "Piece", post: "Post") -> list["Attachment"]:
        """Reconcile ``piece``'s links against the attachments of ``post``.

        Returns the rows in post order so callers can rebuild a display list.

        Reconciles rather than only adding. The legacy ``attachments`` JSON
        this replaces was rebuilt from scratch on every save, so deleting an
        image from a post through the Mastodon API made it disappear from the
        note. Add-only would leave the row linked forever and, because
        ``Note.attachment_list`` prefers rows once any exist, the note card
        would keep showing an image the author removed.

        Pruning is scoped to rows sourced from this post's own attachments.
        Rows from other sources -- a web/API upload (empty ``source``), or a
        copy the backfill made from the legacy JSON of an already-pruned post
        (``takahe-media:``/``url:``) -- are left alone; they are not described
        by ``post.attachments`` and unlinking them here would destroy exactly
        the media that has no other source left.
        """
        owner = piece.owner
        attachments: list[Attachment] = []
        current: set[str] = set()
        for atta in post.attachments.all():
            current.add(source_for_post_attachment(atta.pk))
            current.add(pending_source_for_post_attachment(atta.pk))
            a = cls.from_post_attachment(owner, atta, copy_file=post.local)
            if a:
                attachments.append(a)
        if attachments:
            piece.attachment_records.add(*attachments)
        stale = [
            a
            for a in piece.attachment_records.all()
            if a.source.startswith(("takahe:", "takahe-pending:"))
            and a.source not in current
        ]
        if stale:
            piece.attachment_records.remove(*stale)
        return attachments

    # --- piece linkage ----------------------------------------------------

    @classmethod
    def resolve_body_paths(cls, *texts: str) -> set[str]:
        """Storage-relative paths of the local media embedded in ``texts``.

        External URLs and anything that does not resolve under ``MEDIA_URL``
        are skipped: they are not ours to register.
        """
        paths: set[str] = set()
        for text in texts:
            for m in RE_MD_IMAGE.finditer(text or ""):
                normalized = normalize_image_src(m.group(2).strip())
                if not normalized or not normalized.startswith(settings.MEDIA_URL):
                    continue
                rel_path = normalized[len(settings.MEDIA_URL) :]
                if rel_path:
                    paths.add(rel_path)
        return paths


def is_owned_upload(path: str, owner_id: int) -> bool:
    """True when ``path`` is an upload belonging to ``owner_id``.

    Upload paths are ``upload/<identity_id>/<year>/<uuid>.<ext>``, so the
    owner is readable from the path itself. Registering only the owner's own
    files keeps a hotlink -- user B embedding the URL of user A's image, which
    ``normalize_image_src`` happily accepts -- from claiming A's file for B.
    Without this, deleting B's account would delete A's image, and A deleting
    theirs would silently break B's page. Such a hotlink simply stays
    unregistered, exactly as it was before the registry existed.
    """
    parts = path.split("/")
    return len(parts) > 2 and parts[0] == "upload" and parts[1] == str(owner_id)


def link_attachments_to_piece(piece: "Piece", *texts: str) -> None:
    """Sync ``piece``'s attachment links against the media it embeds.

    Called from the local-author save paths (never from inbound federation).
    Newly referenced uploads are linked, and links to media the edit removed
    are dropped -- the row itself survives, unlinked, so an accidental
    removal is recoverable and a shared upload stays alive for the other
    pieces still using it.

    Registers any embedded path that has no row yet, which keeps content
    written before the registry existed (or restored by an importer) from
    staying invisible to it.
    """
    owner = piece.owner
    paths = {
        p for p in Attachment.resolve_body_paths(*texts) if is_owned_upload(p, owner.pk)
    }
    linked: dict[str, Attachment] = {}
    for a in piece.attachment_records.all():
        name = a.file.name if a.file else None
        if name:
            linked[name] = a
    for path in paths - set(linked):
        attachment = Attachment.objects.filter(file=path).first() or Attachment.adopt(
            owner, path
        )
        if attachment:
            piece.attachment_records.add(attachment)
    stale = [a for name, a in linked.items() if name not in paths]
    if stale:
        piece.attachment_records.remove(*stale)
