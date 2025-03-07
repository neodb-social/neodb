import csv
import os
import tempfile
import zipfile
from typing import Dict

from django.utils import timezone
from loguru import logger

from catalog.models import ItemCategory
from journal.models import Mark, Note, Review

from .base import BaseImporter


class CsvImporter(BaseImporter):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    def import_mark(self, row: Dict[str, str]) -> str:
        """Import a mark from a CSV row.

        Returns:
            str: 'imported', 'skipped', or 'failed' indicating the import result
        """
        try:
            item = self.get_item_by_info_and_links(
                row.get("title", ""),
                row.get("info", ""),
                row.get("links", "").strip().split(),
            )

            if not item:
                logger.error(f"Could not find item: {row.get('links', '')}")
                self.metadata["failed_items"].append(
                    f"Could not find item: {row.get('links', '')}"
                )
                return "failed"

            owner = self.user.identity
            mark = Mark(owner, item)

            shelf_type = self.parse_shelf_type(row.get("status", ""))
            rating_grade = None
            if "rating" in row and row["rating"]:
                try:
                    rating_grade = int(float(row["rating"]))
                except (ValueError, TypeError):
                    pass

            comment_text = row.get("comment", "")
            tags = self.parse_tags(row.get("tags", ""))

            # Parse timestamp
            created_time = (
                self.parse_datetime(row.get("timestamp", "")) or timezone.now()
            )

            if (
                mark.shelf_type
                and mark.created_time
                and mark.created_time >= created_time
            ):
                # skip if existing mark is newer
                return "skipped"

            # Update the mark
            mark.update(
                shelf_type,
                comment_text=comment_text,
                rating_grade=rating_grade,
                tags=tags,
                created_time=created_time,
                visibility=self.metadata.get("visibility", 0),
            )
            return "imported"
        except Exception as e:
            logger.error(f"Error importing mark: {e}")
            self.metadata["failed_items"].append(
                f"Error importing mark for {row.get('title', '')}"
            )
            return "failed"

    def import_review(self, row: Dict[str, str]) -> str:
        """Import a review from a CSV row.

        Returns:
            str: 'imported', 'skipped', or 'failed' indicating the import result
        """
        try:
            item = self.get_item_by_info_and_links(
                row.get("title", ""),
                row.get("info", ""),
                row.get("links", "").strip().split(),
            )

            if not item:
                logger.error(f"Could not find item for review: {row.get('links', '')}")
                self.metadata["failed_items"].append(
                    f"Could not find item for review: {row.get('links', '')}"
                )
                return "failed"

            owner = self.user.identity
            review_title = row.get("title", "")  # Second "title" field is review title
            review_content = row.get("content", "")

            # Parse timestamp
            created_time = self.parse_datetime(row.get("timestamp", ""))

            # Check if there's an existing review with the same or newer timestamp
            existing_review = Review.objects.filter(
                owner=owner, item=item, title=review_title
            ).first()
            # Skip if existing review is newer or same age
            if (
                existing_review
                and existing_review.created_time
                and created_time
                and existing_review.created_time >= created_time
            ):
                logger.debug(
                    f"Skipping review import for {item.display_title}: existing review is newer or same age"
                )
                return "skipped"

            # Create/update the review
            Review.update_item_review(
                item,
                owner,
                review_title,
                review_content,
                created_time=created_time,
                visibility=self.metadata.get("visibility", 0),
            )
            return "imported"
        except Exception as e:
            logger.error(f"Error importing review: {e}")
            self.metadata["failed_items"].append(
                f"Error importing review for {row.get('title', '')}: {str(e)}"
            )
            return "failed"

    def import_note(self, row: Dict[str, str]) -> str:
        """Import a note from a CSV row.

        Returns:
            str: 'imported', 'skipped', or 'failed' indicating the import result
        """
        try:
            item = self.get_item_by_info_and_links(
                row.get("title", ""),
                row.get("info", ""),
                row.get("links", "").strip().split(),
            )

            if not item:
                logger.error(f"Could not find item for note: {row.get('links', '')}")
                self.metadata["failed_items"].append(
                    f"Could not find item for note: {row.get('links', '')}"
                )
                return "failed"

            owner = self.user.identity
            title = row.get("title", "")  # Second "title" field is note title
            content = row.get("content", "")
            progress = row.get("progress", "")

            # Parse timestamp
            created_time = self.parse_datetime(row.get("timestamp", ""))

            # Extract progress information
            pt, pv = Note.extract_progress(progress)

            # Check if a note with the same attributes already exists
            existing_notes = Note.objects.filter(
                item=item,
                owner=owner,
                title=title,
                progress_type=pt,
                progress_value=pv,
            )

            # If we have an exact content match, skip this import
            for existing_note in existing_notes:
                if existing_note.content == content:
                    logger.debug(
                        f"Skipping note import for {item.display_title}: duplicate note found"
                    )
                    return "skipped"

            # Create the note if no duplicate is found
            Note.objects.create(
                item=item,
                owner=owner,
                title=title,
                content=content,
                progress_type=pt,
                progress_value=pv,
                created_time=created_time,
                visibility=self.metadata.get("visibility", 0),
            )
            return "imported"
        except Exception as e:
            logger.error(f"Error importing note: {e}")
            self.metadata["failed_items"].append(
                f"Error importing note for {row.get('title', '')}: {str(e)}"
            )
            return "failed"

    def process_csv_file(self, file_path: str, import_function) -> None:
        """Process a CSV file using the specified import function."""
        logger.debug(f"Processing {file_path}")
        with open(file_path, "r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                result = import_function(row)
                self.progress(result)

    def run(self) -> None:
        """Run the CSV import."""
        filename = self.metadata["file"]
        logger.debug(f"Importing {filename}")

        with zipfile.ZipFile(filename, "r") as zipref:
            with tempfile.TemporaryDirectory() as tmpdirname:
                zipref.extractall(tmpdirname)

                # Count total rows in all CSV files first
                total_rows = 0
                csv_files = []

                for category in [
                    ItemCategory.Movie,
                    ItemCategory.TV,
                    ItemCategory.Music,
                    ItemCategory.Book,
                    ItemCategory.Game,
                    ItemCategory.Podcast,
                    ItemCategory.Performance,
                ]:
                    for file_type in ["mark", "review", "note"]:
                        file_path = os.path.join(
                            tmpdirname, f"{category}_{file_type}.csv"
                        )
                        if os.path.exists(file_path):
                            with open(file_path, "r") as csvfile:
                                row_count = sum(1 for _ in csv.DictReader(csvfile))
                                total_rows += row_count
                                csv_files.append((file_path, file_type))

                # Set the total count in metadata
                self.metadata["total"] = total_rows
                self.message = f"found {total_rows} records to import"
                self.save(update_fields=["metadata", "message"])

                # Now process all files
                for file_path, file_type in csv_files:
                    import_function = getattr(self, f"import_{file_type}")
                    self.process_csv_file(file_path, import_function)

        self.message = f"{self.metadata['imported']} items imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed."
        self.save()
