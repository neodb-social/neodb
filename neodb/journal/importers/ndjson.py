import datetime
import json
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from loguru import logger

from catalog.models import ExternalResource, Item
from catalog.sites.fedi import FediverseInstance
from common.models.lang import detect_language
from common.validators import is_storable_url
from journal.models import (
    Article,
    Attachment,
    Collection,
    Comment,
    Mark,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfMemberProgress,
    ShelfType,
    Tag,
    TagMember,
)
from journal.models.attachment import link_attachments_to_piece
from journal.models.renderers import RE_MD_IMAGE
from takahe.utils import Takahe
from users.models import APIdentity

from .base import BaseImporter


class NdjsonImporter(BaseImporter):
    """Importer for NDJSON files exported from NeoDB."""

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = {}

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        """Reject anything that is not a NeoDB NDJSON archive.

        ``import_neodb`` picks the importer from a client-supplied
        ``format_type`` field, so without this an arbitrary upload only fails
        later in the worker, as an opaque task failure.
        """
        try:
            if not zipfile.is_zipfile(uploaded_file):
                return False
            uploaded_file.seek(0)
            with zipfile.ZipFile(uploaded_file, "r") as z:
                # exactly the path run() opens: a nested
                # "some-folder/journal.ndjson" is not something it can read
                return "journal.ndjson" in z.namelist()
        except Exception:
            return False
        finally:
            try:
                uploaded_file.seek(0)
            except Exception:
                pass

    def _archive_updated(
        self, content_data: Dict[str, Any]
    ) -> datetime.datetime | None:
        """The archive's last-edit stamp, or None when it carries none.

        ``ap_object`` writes ``updated`` from ``edited_time``. Deliberately no
        fallback to ``published``: the two are compared against different
        columns, so the choice belongs in ``_is_current``.
        """
        return self.parse_datetime(content_data.get("updated"))

    def _is_current(
        self,
        existing: Any,
        updated_dt: datetime.datetime | None,
        published_dt: datetime.datetime | None,
    ) -> bool:
        """True when the destination row is already at least as new.

        Prefers ``edited_time`` vs the archive's ``updated``: editing a piece
        bumps only ``edited_time``, so comparing ``created_time`` reports every
        edited record as "not newer" and silently keeps the stale copy.

        A bundle with no ``updated`` gets the original ``created_time`` vs
        ``published`` comparison instead. Mixing the two would be worse than
        either: the destination's ``edited_time`` is a local ``auto_now`` stamp
        with nothing in such a bundle to compare against, so every record
        would read as current and nothing would ever import.
        """
        if existing is None:
            return False
        if updated_dt:
            edited = getattr(existing, "edited_time", None)
            return bool(edited and edited >= updated_dt)
        if published_dt:
            created = getattr(existing, "created_time", None)
            return bool(created and created >= published_dt)
        return False

    def _restore_edited_time(
        self, piece: Any, updated_dt: datetime.datetime | None
    ) -> None:
        """Persist the archive's ``updated`` stamp onto a saved piece.

        ``Content.edited_time`` is ``auto_now`` (and
        ``Article.update_local_article`` assigns its own), so the value has to
        be written past the model the way ``List.apply_ap_envelope`` does.
        Without it the destination row is stamped with the import time, and an
        edit made between two imports compares as older than that stamp and is
        dropped.
        """
        if not updated_dt or piece is None or piece.pk is None:
            return
        type(piece).objects.filter(pk=piece.pk).update(edited_time=updated_dt)
        piece.edited_time = updated_dt

    def _resolve_temp_path(self, rel_path: str | None) -> str | None:
        """Resolve a path relative to self.temp_dir, rejecting traversal.

        Returns the absolute path if it stays within self.temp_dir, else None.
        rel_path comes from user-supplied JSON, so must be validated before use.
        """
        if not rel_path or not hasattr(self, "temp_dir"):
            return None
        base = os.path.realpath(self.temp_dir)
        resolved = os.path.realpath(os.path.join(base, rel_path))
        if resolved != base and not resolved.startswith(base + os.sep):
            logger.warning(f"Rejected path outside import temp dir: {rel_path}")
            return None
        return resolved

    def _store_bundled_file(self, rel_path: str | None) -> str | None:
        """Copy a file out of the extracted bundle into media storage.

        Returns the stored file's media URL, which is MEDIA_URL-prefixed and
        therefore accepted by ``renderers.normalize_image_src``. Returns
        None when the bundle carries no such file.
        """
        src = self._store_path(rel_path)
        if not src:
            return None
        ext = os.path.splitext(src)[1]
        # same layout as journal.views.common.generate_upload_path, so
        # restored images sit where the rest of the app expects uploads
        name = f"upload/{self.user.identity.pk}/{timezone.now():%Y}/{uuid.uuid4()}{ext}"
        with open(src, "rb") as f:
            name = default_storage.save(name, File(f))
        return default_storage.url(name)

    def _store_path(self, rel_path: str | None) -> str | None:
        """Resolved path of a regular file inside the bundle, else None."""
        src = self._resolve_temp_path(rel_path)
        return src if src and os.path.isfile(src) else None

    def _restore_note_attachment(
        self, owner: APIdentity, atta: Dict[str, Any]
    ) -> Attachment | None:
        """Restore one bundled note attachment as a registered upload.

        Registering here rather than reusing ``_store_bundled_file`` keeps the
        bytes stored once: the legacy ``attachments`` JSON entry is derived
        from the row via ``to_json()`` instead of being built separately.

        Falls back to the exporter's recorded URL when the bundle carries no
        file (the post was pruned, or its media lives elsewhere), which keeps
        the attachment rather than dropping it.
        """
        mimetype = atta.get("mimetype", "")
        src = self._store_path(atta.get("file"))
        if src:
            ext = (
                os.path.splitext(src)[1]
                or mimetypes.guess_extension(mimetype)
                or ".bin"
            )
            try:
                with open(src, "rb") as f:
                    return Attachment.register(owner, File(f), ext, mimetype=mimetype)
            except Exception as e:
                logger.warning(f"error registering note attachment {src}: {e}")
        url = atta.get("url")
        if not url:
            return None
        return Attachment.from_legacy_json(owner, {"url": url, "mimetype": mimetype})

    def _restore_body_images(self, body: str, data: Dict[str, Any]) -> str:
        """Repoint inline markdown images at copies restored from the bundle.

        Without this a migrated review / article / collection keeps pointing
        at the source server's media, which 404s for every reader on the
        destination server.
        """
        images = data.get("images") or []
        if not body or not images:
            return body
        mapping: Dict[str, str] = {}
        for entry in images:
            if not isinstance(entry, dict):
                continue
            src = entry.get("src")
            url = self._store_bundled_file(entry.get("file"))
            if src and url:
                mapping[src] = url
        if not mapping:
            return body

        def _replace(m: "re.Match[str]") -> str:
            src = m.group(2).strip()
            return f"![{m.group(1)}]({mapping.get(src, src)})"

        return RE_MD_IMAGE.sub(_replace, body)

    def import_collection(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a collection from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            name = content_data.get("name", "")
            content = content_data.get("content", "")
            # TODO: identity is (owner, title, created_time), so a collection
            # renamed on the source re-imports as a second collection rather
            # than updating this one.
            existing = Collection.objects.filter(
                owner=owner, title=name, created_time=published_dt
            ).first()
            if existing and self._is_current(existing, updated_dt, published_dt):
                return "skipped"
            # after the staleness check, never before: this copies each bundled
            # image into media storage, so a no-op re-import would otherwise
            # leave behind a fresh copy of every inline image
            brief = self._restore_body_images(content, data)
            if existing:
                # a newer archive edits the collection in place; creating a
                # second row would leave the user with a duplicate they then
                # have to reconcile by hand
                existing.title = name
                existing.brief = brief
                existing.visibility = visibility
                existing.metadata = data.get("metadata") or {}
                existing.collaborative = data.get("collaborative", 0)
                existing.query = data.get("query")
                existing.save()
                collection = existing
            else:
                collection = Collection.objects.create(
                    owner=owner,
                    title=name,
                    brief=brief,
                    visibility=visibility,
                    metadata=data.get("metadata") or {},
                    collaborative=data.get("collaborative", 0),
                    query=data.get("query"),
                    # created_time is not nullable; let the model default stand
                    # when a bundle carries no published timestamp
                    **({"created_time": published_dt} if published_dt else {}),
                )
            # register the restored images as tracked uploads; on the update
            # branch this also unlinks the rows the previous body referenced
            link_attachments_to_piece(collection, collection.brief)
            cover_src = self._store_path(data.get("cover"))
            if cover_src:
                with open(cover_src, "rb") as f:
                    collection.cover.save(
                        os.path.basename(cover_src), File(f), save=True
                    )
            item_data = data.get("items", [])
            member_notes_changed = False
            with collection.defer_member_updates():
                for item_entry in item_data:
                    item_url = item_entry.get("item")
                    if not item_url:
                        continue
                    item = self.items.get(item_url)
                    if not item:
                        logger.warning(
                            f"Could not find item for collection: {item_url}"
                        )
                        continue
                    metadata = item_entry.get("metadata", {})
                    member, created = collection.append_item(item, metadata=metadata)
                    if not created and member and member.metadata != metadata:
                        # append_item is a no-op for an item already on the list,
                        # so a per-item note edited on the source would not replay
                        member.metadata = metadata
                        member.save(update_fields=["metadata"])
                        member_notes_changed = True
            # TODO: members removed on the source are not removed here -- a
            # re-import only adds and updates, never deletes. A member note
            # edited on its own also does not replay: it leaves the parent
            # collection's edited_time untouched, so the record is judged
            # current and skipped before this loop is reached.
            self._restore_edited_time(collection, updated_dt)
            if member_notes_changed:
                # NDJSON tasks promise their restored state is searchable when
                # run() returns. Keep the one final refresh synchronous for a
                # note-only edit; member additions remain coalesced and async.
                collection.update_index()
            return "imported"
        except Exception:
            logger.exception("Error importing collection")
            return "failed"

    def import_shelf_member(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a shelf member (mark) from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            shelf_type = content_data.get("status", ShelfType.WISHLIST)
            mark = Mark(owner, item)
            if self._is_current(mark.shelfmember, updated_dt, published_dt):
                return "skipped"
            mark.update(
                shelf_type=shelf_type,
                visibility=visibility,
                metadata=metadata,
                created_time=published_dt,
            )
            self.restore_progress(owner, item, data)
            self._restore_edited_time(mark.shelfmember, updated_dt)
            return "imported"
        except Exception:
            logger.exception("Error importing shelf member")
            return "failed"

    def restore_progress(
        self, owner: APIdentity, item: Item, data: Dict[str, Any]
    ) -> None:
        """Restore — or clear — a mark's current reading progress.

        Tri-state on the ``progress`` key, so that a newer archive in which
        the user cleared their progress can replay that: an absent key is a
        legacy archive that never carried progress and is left alone, a value
        restores it, and an explicit null clears it. Only reached once the
        archive has been found newer than the destination mark.

        Written straight to ``ShelfMemberProgress`` rather than through
        ``Mark.set_progress``: that would append a fresh log entry stamped
        with the import time, polluting the history the ShelfLog records
        restore separately.
        """
        if "progress" not in data:
            return
        shelfmember = ShelfMember.objects.filter(owner=owner, item=item).first()
        if not shelfmember:
            return
        progress = data.get("progress") or {}
        value = progress.get("value")
        if not value:
            ShelfMemberProgress.objects.filter(shelf_member=shelfmember).delete()
            return
        ShelfMemberProgress.objects.update_or_create(
            shelf_member=shelfmember,
            defaults={
                "progress_type": progress.get("type") or None,
                "progress_value": value,
            },
        )

    def import_shelf_log(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a shelf log entry from NDJSON data."""
        try:
            item = self.items.get(data.get("item", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            owner = self.user.identity
            shelf_type = data.get("status", ShelfType.WISHLIST)
            # TODO: data["posts"] carries the source post ids for this entry;
            # relinking them needs the posts themselves, which import_post does
            # not restore yet.
            timestamp = data.get("timestamp")
            timestamp_dt = self.parse_datetime(timestamp) if timestamp else None
            if not timestamp_dt:
                # timestamp is not nullable; inserting would raise
                # IntegrityError and break the enclosing transaction
                raise ValueError(f"Shelf log without timestamp: {data.get('item', '')}")
            # comment_text / rating_grade / progress_* are jsondata fields
            # stored inside metadata. Keyed on presence, not truthiness: an
            # archive that carries the key is authoritative even when it is
            # empty, while a legacy archive without it must not blank what
            # the mark import already wrote.
            defaults = (
                {"metadata": data.get("metadata") or {}} if "metadata" in data else {}
            )
            _, created = ShelfLogEntry.objects.update_or_create(
                owner=owner,
                item=item,
                shelf_type=shelf_type,
                timestamp=timestamp_dt,
                defaults=defaults,
            )
            # return "imported" if created else "skipped"
            # count skip as success otherwise it may confuse user
            return "imported"
        except Exception:
            logger.exception("Error importing shelf log")
            return "failed"

    def import_post(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a post from NDJSON data."""
        # TODO: not implemented. The exporter bundles plain posts (and their
        # attachments) but they are dropped here, so a migration loses the
        # user's non-journal posts.
        return "skipped"

    def import_article(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a standalone Article from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {}) or {}
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            title = (content_data.get("name") or "")[:500]
            source = content_data.get("source") or {}
            if source.get("mediaType") == "text/markdown" and source.get("content"):
                body = source["content"]
            else:
                # Fall back to the rendered HTML if no markdown source — the
                # exporter always sets source, but tolerate older bundles.
                body = content_data.get("content", "") or ""
            summary = content_data.get("summary") or ""
            sensitive = bool(content_data.get("sensitive", False))
            tags: list[str] = []
            for t in content_data.get("tag", []) or []:
                if isinstance(t, dict) and t.get("type") == "Hashtag":
                    name = (t.get("name") or "").lstrip("#")
                    if name:
                        tags.append(name)
            # TODO: identity is (owner, title, created_time), so an article
            # retitled on the source re-imports as a second article rather than
            # updating this one.
            existing = Article.objects.filter(
                owner=owner, title=title, created_time=published_dt
            ).first()
            if existing and self._is_current(existing, updated_dt, published_dt):
                return "skipped"
            body = self._restore_body_images(body, data)
            # Restore the bundled featured image (if any) as part of the
            # initial create so the federated post carries it too. Keep the
            # handle open across the create — the ImageField reads it on save.
            cover_src = self._store_path(data.get("cover"))
            cover_arg = None
            cover_fh = None
            if cover_src:
                cover_fh = open(cover_src, "rb")
                cover_arg = File(cover_fh, name=os.path.basename(cover_src))
            try:
                # ``article=existing`` edits in place: a newer archive of an
                # article already here must not land as a second copy
                article = Article.update_local_article(
                    owner=owner,
                    title=title,
                    body=body,
                    summary=summary,
                    sensitive=sensitive,
                    visibility=visibility,
                    language=data.get("language") or "",
                    tags=tags,
                    cover=cover_arg,
                    article=existing,
                )
            finally:
                if cover_fh is not None:
                    cover_fh.close()
            # ``update_local_article`` overwrites metadata['word_count'];
            # merge any other keys (e.g. author-specific extras) the bundle
            # carried back in.
            extra = {k: v for k, v in metadata.items() if k != "word_count"}
            if extra or published_dt:
                if extra:
                    article.metadata = {**article.metadata, **extra}
                if published_dt:
                    article.created_time = published_dt
                update_fields = []
                if extra:
                    update_fields.append("metadata")
                if published_dt:
                    update_fields.append("created_time")
                article.save(
                    update_fields=update_fields,
                    post_when_save=False,
                    index_when_save=False,
                )
            self._restore_edited_time(article, updated_dt)
            return "imported"
        except Exception:
            logger.exception("Error importing article")
            return "failed"

    def import_review(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a review from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            name = content_data.get("name", "")
            content = content_data.get("content", "")
            # TODO: identity is (owner, item, title), so a review retitled on
            # the source re-imports as a second review rather than updating
            # this one.
            existing_review = Review.objects.filter(
                owner=owner, item=item, title=name
            ).first()
            if existing_review:
                if self._is_current(existing_review, updated_dt, published_dt):
                    return "skipped"
                # a newer export updates the existing review in place;
                # creating another row would duplicate (owner, item, title)
                existing_review.body = self._restore_body_images(content, data)
                if published_dt:
                    existing_review.created_time = published_dt
                existing_review.visibility = visibility
                existing_review.metadata = metadata
                existing_review.save()
                self._restore_edited_time(existing_review, updated_dt)
                link_attachments_to_piece(existing_review, existing_review.body)
                return "imported"
            review = Review.objects.create(
                owner=owner,
                item=item,
                title=name,
                body=self._restore_body_images(content, data),
                visibility=visibility,
                metadata=metadata,
                **({"created_time": published_dt} if published_dt else {}),
            )
            self._restore_edited_time(review, updated_dt)
            link_attachments_to_piece(review, review.body)
            return "imported"
        except Exception:
            logger.exception("Error importing review")
            return "failed"

    def import_note(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a note from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            title = content_data.get("title", "")
            content = content_data.get("content", "")
            sensitive = content_data.get("sensitive", False)
            progress = content_data.get("progress", {})
            progress_type = progress.get("type", "")
            progress_value = progress.get("value", "")
            # a user may hold several notes on one item, so created_time is
            # what identifies this one
            existing = Note.objects.filter(
                owner=owner, item=item, created_time=published_dt
            ).first()
            if existing:
                if self._is_current(existing, updated_dt, published_dt):
                    return "skipped"
                existing.title = title
                existing.content = content
                existing.sensitive = sensitive
                existing.progress_type = progress_type
                existing.progress_value = progress_value
                existing.visibility = visibility
                existing.metadata = data.get("metadata") or {}
                # post and index after the media below is registered, so the
                # timeline post carries it and the index doc has the post id;
                # the final save runs both hooks
                existing.save(post_when_save=False, index_when_save=False)
                note = existing
            else:
                note = Note(
                    item=item,
                    owner=owner,
                    title=title,
                    content=content,
                    sensitive=sensitive,
                    progress_type=progress_type,
                    progress_value=progress_value,
                    visibility=visibility,
                    metadata=data.get("metadata") or {},
                    **({"created_time": published_dt} if published_dt else {}),
                )
                note.save(post_when_save=False, index_when_save=False)
            note_attachments = []
            restored: list[Attachment] = []
            for atta in data.get("attachments") or []:
                if not isinstance(atta, dict):
                    continue
                a = self._restore_note_attachment(owner, atta)
                if not a:
                    continue
                restored.append(a)
                entry = a.to_json()
                if entry["url"].startswith("/"):
                    site = settings.SITE_INFO["site_url"].rstrip("/")
                    entry["url"] = site + entry["url"]
                    if entry["preview_url"].startswith("/"):
                        entry["preview_url"] = site + entry["preview_url"]
                note_attachments.append(entry)
            # set, not add: this path now updates an existing note in place, and
            # each import registers freshly copied files, so adding would grow
            # the note's media on every re-import of an edited record -- and
            # attachment_list prefers rows, so it would render the duplicates.
            # The archive is authoritative for the note's media, the legacy
            # JSON included: attachment_list falls back to it once the rows
            # are gone, so a stale copy would resurrect removed media.
            if restored or note.attachment_records.exists():
                note.attachment_records.set(restored)
            note.attachments = note_attachments
            # the deferred timeline post and index: Note.to_post_params puts
            # exactly the restored rows on the post (clearing media the
            # archive no longer has), then the index doc gets the post id
            note.post_media_from_records = True
            note.save(update_fields=["attachments"])
            self._restore_edited_time(note, updated_dt)
            return "imported"
        except Exception:
            logger.exception("Error importing note")
            return "failed"

    def import_comment(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a comment from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            content = content_data.get("content", "")
            existing_comment = Comment.objects.filter(owner=owner, item=item).first()
            if existing_comment:
                if self._is_current(existing_comment, updated_dt, published_dt):
                    return "skipped"
                # a newer export updates the existing comment in place;
                # creating another row would duplicate (owner, item),
                # the same corruption behind EGGPLANT-1GP
                existing_comment.text = content
                if published_dt:
                    existing_comment.created_time = published_dt
                existing_comment.visibility = visibility
                existing_comment.metadata = metadata
                # no index during import, like import_article/import_note;
                # the mark import covers commented marks, idx-sync covers
                # lone comments (e.g. on episodes), same as before import
                # hooks existed
                existing_comment.save(index_when_save=False)
                self._restore_edited_time(existing_comment, updated_dt)
                return "imported"
            comment = Comment(
                owner=owner,
                item=item,
                text=content,
                visibility=visibility,
                metadata=metadata,
                **({"created_time": published_dt} if published_dt else {}),
            )
            comment.save(index_when_save=False)
            self._restore_edited_time(comment, updated_dt)
            return "imported"
        except Exception:
            logger.exception("Error importing comment")
            return "failed"

    def import_rating(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a rating from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            updated_dt = self._archive_updated(content_data)
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            rating_grade = int(float(content_data.get("value") or 0))
            if not rating_grade:
                # a rating with no grade carries nothing to restore, and
                # Rating.update_item_rating treats it as a deletion
                return "skipped"
            existing_rating = Rating.objects.filter(owner=owner, item=item).first()
            if existing_rating:
                if self._is_current(existing_rating, updated_dt, published_dt):
                    return "skipped"
                # (owner, item) is unique on Rating: inserting a second row
                # raises IntegrityError, which marks the surrounding
                # transaction for rollback and fails every later record too
                existing_rating.grade = rating_grade
                if published_dt:
                    existing_rating.created_time = published_dt
                existing_rating.visibility = visibility
                existing_rating.metadata = metadata
                existing_rating.save()
                self._restore_edited_time(existing_rating, updated_dt)
                return "imported"
            rating = Rating.objects.create(
                owner=owner,
                item=item,
                grade=rating_grade,
                visibility=visibility,
                metadata=metadata,
                **({"created_time": published_dt} if published_dt else {}),
            )
            self._restore_edited_time(rating, updated_dt)
            return "imported"
        except Exception:
            logger.exception("Error importing rating")
            return "failed"

    def import_tag(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import tags from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            pinned = data.get("pinned", self.metadata.get("pinned", False))
            tag_title = Tag.cleanup_title(data.get("name", ""))
            Tag.objects.update_or_create(
                owner=owner,
                title=tag_title,
                defaults={
                    "visibility": visibility,
                    "pinned": pinned,
                },
            )
            return "imported"
        except Exception:
            logger.exception("Error importing tag member")
            return "failed"

    def import_tag_member(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import tags from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata") or {}
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            tag_title = Tag.cleanup_title(content_data.get("tag", ""))
            # created_time is not nullable, so only pass it when the bundle
            # carries one; inserting NULL raises IntegrityError, which marks
            # the surrounding transaction for rollback and fails every later
            # record too
            created_time = {"created_time": published_dt} if published_dt else {}
            tag, _ = Tag.objects.get_or_create(
                owner=owner,
                title=tag_title,
                defaults={
                    "visibility": visibility,
                    "pinned": False,
                    "metadata": metadata,
                    **created_time,
                },
            )
            _, created = TagMember.objects.update_or_create(
                owner=owner,
                item=item,
                parent=tag,
                defaults={
                    "visibility": visibility,
                    "metadata": metadata,
                    "position": 0,
                    **created_time,
                },
            )
            return "imported" if created else "skipped"
        except Exception:
            logger.exception("Error importing tag member")
            return "failed"

    def import_funcs(
        self,
    ) -> dict[str, Callable[[Dict[str, Any]], BaseImporter.ImportResult]]:
        """Handler per journal record ``type``, in dependency order.

        Keys must cover every ``type`` NdjsonExporter writes; iteration order
        is the import order (Tag before TagMember, Rating/Comment before
        ShelfMember, which reads them back through Mark).
        """
        return {
            "Tag": self.import_tag,
            "TagMember": self.import_tag_member,
            "Rating": self.import_rating,
            "Comment": self.import_comment,
            "ShelfMember": self.import_shelf_member,
            "Review": self.import_review,
            "Note": self.import_note,
            "Collection": self.import_collection,
            "ShelfLog": self.import_shelf_log,
            # the exporter writes lowercase "post"; "Post" is accepted too so
            # the two never silently drift apart again
            "post": self.import_post,
            "Post": self.import_post,
            "Article": self.import_article,
        }

    def process_journal(self, file_path: str) -> None:
        """Process a NDJSON file and import all items."""
        logger.debug(f"Processing {file_path}")
        lines_error = 0
        import_funcs = self.import_funcs()
        journal: dict[str, list[Dict[str, Any]]] = {k: [] for k in import_funcs.keys()}
        with open(file_path, "r") as jsonfile:
            # Skip header line
            next(jsonfile, None)

            for line in jsonfile:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    lines_error += 1
                    continue
                data_type = data.get("type")
                if not data_type:
                    continue
                if data_type not in journal:
                    journal[data_type] = []
                journal[data_type].append(data)

        self.metadata["total"] = sum(len(items) for items in journal.values())
        self.message = f"found {self.metadata['total']} records to import"
        self.save(update_fields=["metadata", "message"])

        logger.debug(f"Processing {self.metadata['total']} entries")
        if lines_error:
            logger.error(f"Error processing journal.ndjson: {lines_error} lines")

        # journal is seeded from import_funcs, so iterating it preserves the
        # dependency order (Tag before TagMember, Rating/Comment before
        # ShelfMember) and puts unrecognised types last. Every record is
        # accounted for, including unrecognised ones — otherwise processed
        # never reaches total and progress stalls short of 100%.
        for typ, entries in journal.items():
            func = import_funcs.get(typ)
            for data in entries:
                if func is None:
                    logger.debug(f"Skipping unsupported record type {typ}")
                    self.progress("skipped")
                else:
                    self.progress(func(data))
        logger.info(
            f"Imported {self.metadata['imported']}, skipped {self.metadata['skipped']}, failed {self.metadata['failed']}"
        )

    @staticmethod
    def _catalog_localized_title(data: Dict[str, Any]) -> list[dict[str, str]] | None:
        """The entry's localized_title, or one synthesized from its title.

        None when the entry names the item nowhere: the AP schema validation
        in create_from_external_resource rejects a nameless item.
        """
        titles = [
            {"lang": t["lang"], "text": t["text"]}
            for t in data.get("localized_title") or []
            if isinstance(t, dict) and t.get("lang") and t.get("text")
        ]
        if titles:
            return titles
        title = data.get("title") or data.get("display_title")
        if isinstance(title, str) and title.strip():
            return [{"lang": detect_language(title), "text": title.strip()}]
        return None

    @staticmethod
    def _existing_item_for_ids(lookup_ids: Dict[str, str]) -> Item | None:
        """An item already in this catalog carrying any of these ids."""
        for id_type, id_value in lookup_ids.items():
            if not id_value:
                continue
            resource = ExternalResource.objects.filter(
                id_type=id_type, id_value=id_value
            ).first()
            if resource and resource.item and not resource.item.is_deleted:
                return resource.item
            item = Item.objects.filter(
                primary_lookup_id_type=id_type, primary_lookup_id_value=id_value
            ).first()
            if item and not item.is_deleted:
                return item
        return None

    def create_item_from_catalog_data(self, data: Dict[str, Any]) -> Item | None:
        """Build a catalog item out of the metadata bundled in catalog.ndjson.

        Last resort, once every link in the entry has failed to resolve. The
        entry is the same payload FediverseInstance would have downloaded, so
        it is replayed through that path with the fetch skipped. None when the
        entry is too thin to build from, leaving the caller unchanged.
        """
        url = data.get("id")
        # is_storable_url: a hostname that no longer resolves is the case this
        # exists for, and nothing here dereferences the url
        if not isinstance(url, str) or not is_storable_url(url):
            logger.debug(f"Catalog entry has no usable id: {data.get('id')!r}")
            return None
        if FediverseInstance.is_local_item_url(url):
            # unresolved local url means deleted, not missing
            logger.debug(f"Not recreating local item {url}")
            return None
        # checks validate_url_fallback would have made; building the site
        # directly skips it
        host = urlparse(url).hostname or ""
        if host in Takahe.get_blocked_peers():
            logger.debug(f"Not recreating item from blocked peer {host}")
            return None
        typ = data.get("type")
        if not isinstance(typ, str) or typ.lower() not in (
            FediverseInstance.supported_types
        ):
            logger.debug(f"Catalog entry {url} has unsupported type {typ!r}")
            return None
        titles = self._catalog_localized_title(data)
        if not titles:
            logger.debug(f"Catalog entry {url} has no title")
            return None
        data = dict(data, localized_title=titles)
        try:
            # atomic: the Item is saved before it is validated, so a later
            # raise would strand an item-less row, one more per reimport
            with transaction.atomic():
                site = FediverseInstance(url=url)
                content = site.content_from_json(data, detect_redirection=False)
                # going on would reach match_and_link_item, merging this into
                # a shared item the uploader may not be allowed to edit
                existing = self._existing_item_for_ids(content.lookup_ids)
                if existing:
                    logger.debug(f"Catalog entry {url} matches existing {existing}")
                    return existing
                # get_resource_ready links the item itself; asking again would
                # re-run the match for nothing
                resource = site.get_resource_ready(preloaded_content=content)
                item = resource.item if resource else None
        except Exception:
            logger.exception(f"Error creating item from catalog data for {url}")
            return None
        if item:
            logger.info(f"Created {item} from bundled catalog metadata")
        else:
            logger.error(f"Unable to create item from catalog data for {url}")
        return item

    def parse_catalog(self, file_path: str) -> None:
        """Parse the catalog.ndjson file and build item lookup tables."""
        logger.debug(f"Parsing catalog file: {file_path}")
        item_count = 0
        try:
            with open(file_path, "r") as jsonfile:
                for line in jsonfile:
                    # the whole body is guarded, not just the parse: a single
                    # unresolvable entry must not abort the catalog, or every
                    # later piece fails to find its item
                    try:
                        i = json.loads(line)
                        u = i.get("id")
                        if not u:
                            continue
                        item_count += 1
                        links = [u] + [
                            r["url"]
                            for r in i.get("external_resources") or []
                            if isinstance(r, dict) and r.get("url")
                        ]
                        # bundled ids, so an item already here is matched by
                        # the ordinary lookup
                        info = " ".join(
                            f"{k}:{i[k]}"
                            for k in ("isbn", "imdb")
                            if isinstance(i.get(k), str) and i[k]
                        )
                        item = self.get_item_by_info_and_links("", info, links)
                        if not item:
                            # every link failed, so rebuild from the bundled
                            # entry rather than failing every record for it
                            item = self.create_item_from_catalog_data(i)
                        self.items[u] = item
                    except Exception:
                        logger.exception("Error processing catalog item")
            logger.info(f"Loaded {item_count} items from catalog")
            self.metadata["catalog_processed"] = item_count
        except Exception:
            logger.exception("Error parsing catalog file")

    def parse_header(self, file_path: str) -> Dict[str, Any]:
        try:
            with open(file_path, "r") as jsonfile:
                first_line = jsonfile.readline().strip()
                if first_line:
                    header = json.loads(first_line)
                    if header.get("server"):
                        return header
        except json.JSONDecodeError, IOError:
            logger.exception("Error parsing header")
        return {}

    def process_actor(self, file_path: str) -> None:
        """Process the actor.ndjson file to update user identity information.

        TODO: only name and summary are restored. The bundle also carries the
        identity's ``metadata`` (profile fields) and key pair, and carries
        neither avatar nor header; see NdjsonExporter's actor.ndjson block.
        """
        logger.debug(f"Processing actor data from {file_path}")
        try:
            with open(file_path, "r") as jsonfile:
                next(jsonfile, None)
                for line in jsonfile:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.error("Error parsing actor data line")
                        continue

                    if data.get("type") == "Identity":
                        logger.debug("Found identity data in actor.ndjson")
                        takahe_identity = self.user.identity.takahe_identity
                        updated = False
                        if (
                            data.get("name")
                            and data.get("name") != takahe_identity.name
                        ):
                            logger.debug(
                                f"Updating identity name from {takahe_identity.name} to {data.get('name')}"
                            )
                            takahe_identity.name = data.get("name")
                            updated = True
                        if (
                            data.get("summary")
                            and data.get("summary") != takahe_identity.summary
                        ):
                            logger.debug("Updating identity summary")
                            takahe_identity.summary = data.get("summary")
                            updated = True
                        if updated:
                            takahe_identity.save()
                            Takahe.update_state(takahe_identity, "edited")
                            logger.info("Updated identity")
                            return
        except Exception as e:
            logger.exception(f"Error processing actor file: {e}")

    def run(self) -> None:
        """Run the NDJSON import."""
        filename = self.metadata["file"]
        logger.debug(f"Importing {filename}")

        with zipfile.ZipFile(filename, "r") as zipref:
            with tempfile.TemporaryDirectory() as tmpdirname:
                for member in zipref.namelist():
                    member_path = os.path.realpath(os.path.join(tmpdirname, member))
                    if not member_path.startswith(
                        os.path.realpath(tmpdirname) + os.sep
                    ) and member_path != os.path.realpath(tmpdirname):
                        raise ValueError(
                            f"Zip member {member} would extract outside target directory"
                        )
                zipref.extractall(tmpdirname)

                # Process actor data first if available
                actor_path = os.path.join(tmpdirname, "actor.ndjson")
                if os.path.exists(actor_path):
                    actor_header = self.parse_header(actor_path)
                    logger.debug(f"Found actor.ndjson with {actor_header}")
                    self.process_actor(actor_path)
                else:
                    logger.debug("No actor.ndjson file found in the archive")

                catalog_path = os.path.join(tmpdirname, "catalog.ndjson")
                if os.path.exists(catalog_path):
                    catalog_header = self.parse_header(catalog_path)
                    logger.debug(f"Loading catalog.ndjson with {catalog_header}")
                    self.parse_catalog(catalog_path)
                else:
                    logger.warning("catalog.ndjson file not found in the archive")

                journal_path = os.path.join(tmpdirname, "journal.ndjson")
                if not os.path.exists(journal_path):
                    logger.error("journal.ndjson file not found in the archive")
                    self.message = "Import failed: journal.ndjson file not found"
                    self.save()
                    return
                header = self.parse_header(journal_path)
                self.metadata["journal_header"] = header
                logger.debug(f"Importing journal.ndjson with {header}")
                self.temp_dir = tmpdirname
                self.process_journal(journal_path)

        self.message = f"{self.metadata['imported']} items imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed."
        self.save()
