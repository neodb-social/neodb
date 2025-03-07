import json
import os
import tempfile
import zipfile
from typing import Any, Dict

from loguru import logger

from journal.models import (
    Collection,
    Comment,
    Mark,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfType,
    Tag,
    TagMember,
)

from .base import BaseImporter


class NdjsonImporter(BaseImporter):
    """Importer for NDJSON files exported from NeoDB."""

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = {}

    def import_collection(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a collection from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            name = content_data.get("name", "")
            content = content_data.get("content", "")
            collection = Collection.objects.create(
                owner=owner,
                title=name,
                brief=content,
                visibility=visibility,
                metadata=data.get("metadata", {}),
                created_time=published_dt,
            )
            item_data = data.get("items", [])
            for item_entry in item_data:
                item_url = item_entry.get("item")
                if not item_url:
                    continue
                item = self.items.get(item_url)
                if not item:
                    logger.warning(f"Could not find item for collection: {item_url}")
                    continue
                metadata = item_entry.get("metadata", {})
                collection.append_item(item, metadata=metadata)
            return "imported"
        except Exception:
            logger.exception("Error importing collection")
            return "failed"

    def import_shelf_member(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a shelf member (mark) from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            shelf_type = content_data.get("status", ShelfType.WISHLIST)
            mark = Mark(owner, item)
            if mark.created_time and published_dt and mark.created_time >= published_dt:
                return "skipped"
            mark.update(
                shelf_type=shelf_type,
                visibility=visibility,
                metadata=metadata,
                created_time=published_dt,
            )
            return "imported"
        except Exception:
            logger.exception("Error importing shelf member")
            return "failed"

    def import_shelf_log(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a shelf log entry from NDJSON data."""
        try:
            item = self.items.get(data.get("item", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            owner = self.user.identity
            shelf_type = data.get("status", ShelfType.WISHLIST)
            # posts = data.get("posts", [])  # TODO but will be tricky
            timestamp = data.get("timestamp")
            timestamp_dt = self.parse_datetime(timestamp) if timestamp else None
            _, created = ShelfLogEntry.objects.update_or_create(
                owner=owner,
                item=item,
                shelf_type=shelf_type,
                timestamp=timestamp_dt,
            )
            # return "imported" if created else "skipped"
            # count skip as success otherwise it may confuse user
            return "imported"
        except Exception:
            logger.exception("Error importing shelf log")
            return "failed"

    def import_post(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a post from NDJSON data."""
        # TODO
        return "skipped"

    def import_review(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a review from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            name = content_data.get("name", "")
            content = content_data.get("content", "")
            existing_review = Review.objects.filter(
                owner=owner, item=item, title=name
            ).first()
            if (
                existing_review
                and existing_review.created_time
                and published_dt
                and existing_review.created_time >= published_dt
            ):
                return "skipped"
            Review.objects.create(
                owner=owner,
                item=item,
                title=name,
                body=content,
                created_time=published_dt,
                visibility=visibility,
                metadata=metadata,
            )
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
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            title = content_data.get("title", "")
            content = content_data.get("content", "")
            sensitive = content_data.get("sensitive", False)
            progress = content_data.get("progress", {})
            progress_type = progress.get("type", "")
            progress_value = progress.get("value", "")
            Note.objects.create(
                item=item,
                owner=owner,
                title=title,
                content=content,
                sensitive=sensitive,
                progress_type=progress_type,
                progress_value=progress_value,
                created_time=published_dt,
                visibility=visibility,
                metadata=data.get("metadata", {}),
            )
            return "imported"
        except Exception:
            logger.exception("Error importing note")
            return "failed"

    def import_comment(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a comment from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            content = content_data.get("content", "")
            existing_comment = Comment.objects.filter(owner=owner, item=item).first()
            if (
                existing_comment
                and existing_comment.created_time
                and published_dt
                and existing_comment.created_time >= published_dt
            ):
                return "skipped"
            Comment.objects.create(
                owner=owner,
                item=item,
                text=content,
                created_time=published_dt,
                visibility=visibility,
                metadata=metadata,
            )
            return "imported"
        except Exception:
            logger.exception("Error importing comment")
            return "failed"

    def import_rating(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import a rating from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            rating_grade = int(float(content_data.get("value", 0)))
            existing_rating = Comment.objects.filter(owner=owner, item=item).first()
            if (
                existing_rating
                and existing_rating.created_time
                and published_dt
                and existing_rating.created_time >= published_dt
            ):
                return "skipped"
            Rating.objects.create(
                owner=owner,
                item=item,
                grade=rating_grade,
                created_time=published_dt,
                visibility=visibility,
                metadata=metadata,
            )
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
            _, created = Tag.objects.update_or_create(
                owner=owner,
                title=tag_title,
                defaults={
                    "visibility": visibility,
                    "pinned": pinned,
                },
            )
            return "imported" if created else "skipped"
        except Exception:
            logger.exception("Error importing tag member")
            return "failed"

    def import_tag_member(self, data: Dict[str, Any]) -> BaseImporter.ImportResult:
        """Import tags from NDJSON data."""
        try:
            owner = self.user.identity
            visibility = data.get("visibility", self.metadata.get("visibility", 0))
            metadata = data.get("metadata", {})
            content_data = data.get("content", {})
            published_dt = self.parse_datetime(content_data.get("published"))
            item = self.items.get(content_data.get("withRegardTo", ""))
            if not item:
                raise KeyError(f"Could not find item: {data.get('item', '')}")
            tag_title = Tag.cleanup_title(content_data.get("tag", ""))
            tag, _ = Tag.objects.get_or_create(
                owner=owner,
                title=tag_title,
                defaults={
                    "created_time": published_dt,
                    "visibility": visibility,
                    "pinned": False,
                    "metadata": metadata,
                },
            )
            _, created = TagMember.objects.update_or_create(
                owner=owner,
                item=item,
                parent=tag,
                defaults={
                    "created_time": published_dt,
                    "visibility": visibility,
                    "metadata": metadata,
                    "position": 0,
                },
            )
            return "imported" if created else "skipped"
        except Exception:
            logger.exception("Error importing tag member")
            return "failed"

    def process_journal(self, file_path: str) -> None:
        """Process a NDJSON file and import all items."""
        logger.debug(f"Processing {file_path}")
        lines_error = 0
        import_funcs = {
            "Tag": self.import_tag,
            "TagMember": self.import_tag_member,
            "Rating": self.import_rating,
            "Comment": self.import_comment,
            "ShelfMember": self.import_shelf_member,
            "Review": self.import_review,
            "Note": self.import_note,
            "Collection": self.import_collection,
            "ShelfLog": self.import_shelf_log,
            "Post": self.import_post,
        }
        journal = {k: [] for k in import_funcs.keys()}
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

        for typ, func in import_funcs.items():
            for data in journal.get(typ, []):
                result = func(data)
                self.progress(result)
        logger.info(
            f"Imported {self.metadata['imported']}, skipped {self.metadata['skipped']}, failed {self.metadata['failed']}"
        )

    def parse_catalog(self, file_path: str) -> None:
        """Parse the catalog.ndjson file and build item lookup tables."""
        logger.debug(f"Parsing catalog file: {file_path}")
        item_count = 0
        try:
            with open(file_path, "r") as jsonfile:
                for line in jsonfile:
                    try:
                        i = json.loads(line)
                    except (json.JSONDecodeError, Exception):
                        logger.exception("Error processing catalog item")
                        continue
                    u = i.get("id")
                    if not u:
                        continue
                    # self.catalog_items[u] = i
                    item_count += 1
                    links = [u] + [r["url"] for r in i.get("external_resources", [])]
                    self.items[u] = self.get_item_by_info_and_links("", "", links)
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
        except (json.JSONDecodeError, IOError):
            logger.exception("Error parsing header")
        return {}

    def run(self) -> None:
        """Run the NDJSON import."""
        filename = self.metadata["file"]
        logger.debug(f"Importing {filename}")

        with zipfile.ZipFile(filename, "r") as zipref:
            with tempfile.TemporaryDirectory() as tmpdirname:
                zipref.extractall(tmpdirname)

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
                self.process_journal(journal_path)

        self.message = f"{self.metadata['imported']} items imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed."
        self.save()
