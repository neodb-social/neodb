import json
import os
import shutil
import tempfile
import uuid
from typing import Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models.fields.files import ImageFieldFile
from django.utils import timezone
from loguru import logger

from catalog.common import ProxiedImageDownloader
from common.utils import GenerateDateUUIDMediaFilePath
from journal.models import (
    Article,
    Collection,
    Comment,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfMemberProgress,
    Tag,
    TagMember,
)
from journal.models.renderers import RE_MD_IMAGE, normalize_image_src
from takahe.models import Post
from users.models import Task

# Content subclasses carried on the journal stream, in the order they are
# written. Enumerated rather than walked via ``Content.__subclasses__()``:
# that also yields ``Debris`` (tombstones left by catalog item merges), which
# has no ``ap_object`` and used to abort the whole export with
# NotImplementedError for any user who had one.
_CONTENT_CLASSES = (Rating, Comment, Review, Note)


class NdjsonExporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "export"
    DefaultMetadata = {
        "file": None,
        "total": 0,
    }

    @property
    def filename(self) -> str:
        d = self.created_time.strftime("%Y%m%d%H%M%S")
        return f"neodb_{self.user.username}_{d}_ndjson"

    def ref(self, item) -> str:
        if item not in self.ref_items:
            self.ref_items.append(item)
        return item.absolute_url

    def get_header(self):
        return {
            "server": settings.SITE_DOMAIN,
            "neodb_version": settings.NEODB_VERSION,
            "username": self.user.username,
            "actor": self.user.identity.actor_uri,
            "request_time": self.created_time.isoformat(),
            "created_time": timezone.now().isoformat(),
        }

    # --- bundling helpers -------------------------------------------------
    #
    # Every helper returns an archive-relative path ("attachments/xxx.png")
    # so the importer can restore the file and repoint the record at it.

    def _bundle_path(self, filename: str) -> tuple[str, str]:
        """Absolute destination and archive-relative path for ``filename``,
        uniquified so two sources with the same basename can't clobber."""
        dest = os.path.join(self.attachment_path, filename)
        if os.path.exists(dest):
            filename = f"{uuid.uuid4()}-{filename}"
            dest = os.path.join(self.attachment_path, filename)
        return dest, f"attachments/{filename}"

    def _save_image(self, url: str) -> str | None:
        """Copy (local) or download (remote) an image into the bundle.

        Returns its archive-relative path, or None when it can't be bundled.
        Local media is resolved first, via the renderer's normalizer so that
        an absolute URL on our own site (how import_note records restored
        attachments) is recognised as local: pulling our own files back over
        HTTP is wasteful, and is_valid_url blocks it outright when the site
        or media host is internal.
        """
        cached = self.bundled_images.get(url)
        if cached is not None:
            return cached or None
        path = None
        normalized = normalize_image_src(url) or url
        if normalized.startswith(settings.MEDIA_URL):
            rel_path = normalized[len(settings.MEDIA_URL) :]
            basename = os.path.basename(rel_path)
            if basename:
                dest, path = self._bundle_path(basename)
                try:
                    with default_storage.open(rel_path, "rb") as src:
                        with open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                except Exception:
                    logger.error(f"error copying {url} to {self.attachment_path}")
                    path = None
        elif normalized.startswith("http"):
            try:
                raw_img, ext = ProxiedImageDownloader.download_image(normalized, "")
                if raw_img:
                    dest, path = self._bundle_path(f"{uuid.uuid4()}.{ext or 'jpg'}")
                    with open(dest, "wb") as binary_file:
                        binary_file.write(raw_img)
            except Exception:
                logger.debug(f"error downloading {url}")
                path = None
        # cache misses too, so a broken URL is only attempted once per export
        self.bundled_images[url] = path or ""
        return path

    def _bundle_body_images(self, body: str) -> list[dict[str, str]]:
        """Bundle every inline markdown image in ``body``.

        Returns ``[{"src": <original>, "file": <archive path>}]`` for the
        images that were archived; the importer uses this to rewrite the
        body, without which local images 404 on the destination server.
        """
        images: list[dict[str, str]] = []
        seen: set[str] = set()
        # RE_MD_IMAGE is the renderer's own pattern; the previous local one
        # only matched an empty alt text, silently skipping ``![alt](url)``
        for m in RE_MD_IMAGE.finditer(body or ""):
            src = m.group(2).strip()
            if not src or src in seen:
                continue
            seen.add(src)
            path = self._save_image(src)
            if path:
                images.append({"src": src, "file": path})
        return images

    def _bundle_cover(self, cover: ImageFieldFile) -> str | None:
        """Bundle a piece's cover image file (Article / Collection).

        The ap_object only carries a URL that may be unreachable after
        migration, so the bytes travel in the archive.
        """
        if not cover or str(cover) == settings.DEFAULT_ITEM_COVER:
            return None
        basename = os.path.basename(str(cover))
        if not basename:
            return None
        dest, path = self._bundle_path(basename)
        try:
            with cover.open("rb") as src:
                with open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        except Exception as e:
            logger.error(
                f"error copying cover {basename} to {dest}", extra={"exception": e}
            )
            return None
        return path

    def _bundle_post_attachments(self, post: Post) -> list[dict[str, str]]:
        attachments = []
        for a in post.attachments.all():
            basename = os.path.basename(a.file.name or "")
            if not basename:
                continue
            dest, path = self._bundle_path(basename)
            try:
                with a.file.open("rb") as src:
                    with open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            except Exception as e:
                logger.error(
                    f"error copying attachment {basename} to {dest}",
                    extra={"exception": e},
                )
                continue
            attachments.append({"file": path, "mimetype": a.mimetype})
        return attachments

    def _bundle_registered_attachments(self, note: Note) -> list[dict[str, str]]:
        """Bundle a Note's registered uploads (``journal.Attachment`` rows).

        Rows are the only source that survives takahe pruning the post: the
        registry holds NeoDB's own copy of the media under ``upload/``, while
        the legacy JSON still points at the takahe file that died with the
        post. Pointer rows (remote media we never downloaded) carry a URL
        only, so they export like the legacy entries did.
        """
        attachments = []
        for a in note.attachment_records.all():
            url = a.url
            if not url:
                continue
            entry = {"mimetype": a.mimetype or "", "url": url}
            path = self._save_image(url)
            if path:
                entry["file"] = path
            attachments.append(entry)
        return attachments

    def _bundle_note_attachments(self, note: Note) -> list[dict[str, str]]:
        """Attachment records for a Note, in descending order of fidelity.

        1. the linked post, which holds the original files;
        2. the upload registry, which holds our own copy of them -- the only
           source left once takahe has pruned the post;
        3. the legacy ``attachments`` JSON, for notes the async backfill has
           not reached yet.

        Trying the registry before the JSON matters: a pruned post leaves the
        JSON pointing at takahe media that no longer exists, which would
        export as a URL with no file and restore as a dead link.
        """
        if note.latest_post:
            attachments = self._bundle_post_attachments(note.latest_post)
            if attachments:
                return attachments
        attachments = self._bundle_registered_attachments(note)
        if attachments:
            return attachments
        for a in note.attachments or []:
            url = a.get("url")
            if not url:
                continue
            entry = {"mimetype": a.get("mimetype", ""), "url": url}
            path = self._save_image(url)
            if path:
                entry["file"] = path
            attachments.append(entry)
        return attachments

    def run(self):
        self.ref_items = []
        self.bundled_images: dict[str, str] = {}
        user = self.user
        temp_dir = tempfile.mkdtemp()
        temp_folder_path = os.path.join(temp_dir, self.filename)
        os.makedirs(temp_folder_path)
        self.attachment_path = os.path.join(temp_folder_path, "attachments")
        os.makedirs(self.attachment_path, exist_ok=True)

        journal_file = os.path.join(temp_folder_path, "journal.ndjson")
        total = 0
        with open(journal_file, "w") as f:
            f.write(json.dumps(self.get_header()) + "\n")

            for cls in _CONTENT_CLASSES:
                for p in cls.objects.filter(owner=user.identity):
                    total += 1
                    self.ref(p.item)
                    o: dict[str, Any] = {
                        "type": p.__class__.__name__,
                        "content": p.ap_object,
                        "visibility": p.visibility,
                        "metadata": p.metadata,
                    }
                    if isinstance(p, Review):
                        images = self._bundle_body_images(p.body)
                        if images:
                            o["images"] = images
                    elif isinstance(p, Note):
                        attachments = self._bundle_note_attachments(p)
                        if attachments:
                            o["attachments"] = attachments
                    f.write(json.dumps(o, default=str) + "\n")

            # Articles are item-less so they don't fall under
            # _CONTENT_CLASSES. Serialized via the same
            # {type, content, visibility, metadata} envelope the importer
            # expects, plus the bundled body images and featured image.
            for art in Article.objects.filter(owner=user.identity):
                total += 1
                o = {
                    "type": "Article",
                    "content": art.ap_object,
                    "visibility": art.visibility,
                    "metadata": art.metadata,
                    "cover": self._bundle_cover(art.cover),
                }
                images = self._bundle_body_images(art.body)
                if images:
                    o["images"] = images
                f.write(json.dumps(o, default=str) + "\n")

            for c in Collection.objects.filter(owner=user.identity):
                total += 1
                o = {
                    "type": "Collection",
                    "content": c.ap_object,
                    "visibility": c.visibility,
                    "metadata": c.metadata,
                    "collaborative": c.collaborative,
                    "query": c.query,
                    "cover": self._bundle_cover(c.cover),
                    "items": [
                        {"item": self.ref(m.item), "metadata": m.metadata}
                        for m in c.ordered_members
                    ],
                }
                # the description is markdown and may embed images too
                images = self._bundle_body_images(c.brief)
                if images:
                    o["images"] = images
                f.write(json.dumps(o, default=str) + "\n")

            for t in Tag.objects.filter(owner=user.identity):
                total += 1
                o = {
                    "type": "Tag",
                    "name": t.title,
                    "visibility": t.visibility,
                    "pinned": t.pinned,
                }
                f.write(json.dumps(o, default=str) + "\n")

            for t in TagMember.objects.filter(owner=user.identity):
                total += 1
                self.ref(t.item)
                o = {
                    "type": "TagMember",
                    "content": t.ap_object,
                    "visibility": t.visibility,
                    "metadata": t.metadata,
                }
                f.write(json.dumps(o, default=str) + "\n")

            progress_by_member = {
                p.shelf_member_id: p
                for p in ShelfMemberProgress.objects.filter(
                    shelf_member__owner=user.identity
                )
            }
            for m in ShelfMember.objects.filter(owner=user.identity):
                total += 1
                # a mark whose item has no comment/rating/log would otherwise
                # never reach catalog.ndjson, and fail to import
                self.ref(m.item)
                o = {
                    "type": "ShelfMember",
                    "content": m.ap_object,
                    "visibility": m.visibility,
                    "metadata": m.metadata,
                }
                progress = progress_by_member.get(m.pk)
                # always written, null included: a later import has to tell
                # "this mark has no progress" from "this archive predates the
                # field", or clearing progress could never be replayed
                o["progress"] = (
                    {
                        "type": progress.progress_type or "",
                        "value": progress.progress_value,
                    }
                    if progress and progress.progress_value
                    else None
                )
                f.write(json.dumps(o, default=str) + "\n")

            for log in ShelfLogEntry.objects.filter(owner=user.identity):
                total += 1
                o = {
                    "type": "ShelfLog",
                    "item": self.ref(log.item),
                    "status": log.shelf_type,
                    # jsondata fields (comment_text, rating_grade, progress_*)
                    # live in metadata; without it the history reimports blank
                    "metadata": log.metadata,
                    "posts": list(log.all_post_ids()),
                    "timestamp": log.timestamp,
                }
                f.write(json.dumps(o, default=str) + "\n")

            posts = Post.objects.filter(author_id=user.identity.pk).exclude(
                type_data__has_key="object"
            )
            for p in posts:
                total += 1
                o = {"type": "post", "post": p.to_mastodon_json()}
                attachments = self._bundle_post_attachments(p)
                if attachments:
                    o["attachments"] = attachments
                f.write(json.dumps(o, default=str) + "\n")

        catalog_file = os.path.join(temp_folder_path, "catalog.ndjson")
        with open(catalog_file, "w") as f:
            f.write(json.dumps(self.get_header()) + "\n")
            for item in self.ref_items:
                f.write(json.dumps(item.ap_object, default=str) + "\n")

        # Export actor.ndjson with Takahe identity data
        actor_file = os.path.join(temp_folder_path, "actor.ndjson")
        with open(actor_file, "w") as f:
            f.write(json.dumps(self.get_header()) + "\n")
            takahe_identity = self.user.identity.takahe_identity
            identity_data = {
                "type": "Identity",
                "username": takahe_identity.username,
                "domain": takahe_identity.domain_id,
                "actor_uri": takahe_identity.actor_uri,
                "name": takahe_identity.name,
                "summary": takahe_identity.summary,
                "metadata": takahe_identity.metadata,
                "private_key": takahe_identity.private_key,
                "public_key": takahe_identity.public_key,
                "public_key_id": takahe_identity.public_key_id,
            }
            f.write(json.dumps(identity_data, default=str) + "\n")

        filename = GenerateDateUUIDMediaFilePath(
            "f.zip", settings.MEDIA_ROOT + "/" + settings.EXPORT_FILE_PATH_ROOT
        )
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))
        shutil.make_archive(filename[:-4], "zip", temp_folder_path)
        # the staging copy holds every attachment we just bundled; drop it
        # so exports don't accumulate in the system temp dir
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.metadata["file"] = filename
        self.metadata["total"] = total
        self.message = f"{total} records exported."
        self.save()
