import datetime
import zipfile
from typing import Optional

from loguru import logger

from journal.models import Mark, Review

from .csv import CsvImporter


class DoubakImporter(CsvImporter):
    """Import a Douban archive produced by Doubak.

    Doubak (https://doubak.com) captures a Douban account in the user's own
    browser and writes the same ``<category>_<type>.csv`` layout that NeoDB
    exports, so the rows themselves are parsed by :class:`CsvImporter` and
    nothing about the format is restated here.

    What this importer adds is a choice about existing data. A Douban archive is
    a second, independent record of the same account, so the user may want it to
    defer to whatever is already on the shelf, or to replace it. ``MERGE`` keeps
    whichever record is newer; ``OVERWRITE`` applies every row in the archive.

    Notes are deduplicated by content in both modes: they are appended rather
    than replaced, so importing an identical note again would add a second copy
    rather than overwrite anything.
    """

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    MERGE = 0
    OVERWRITE = 1

    DefaultMetadata = CsvImporter.DefaultMetadata | {"mode": MERGE}

    #: an archive is recognised by holding at least one file named like these
    CsvFileSuffixes = ("_mark.csv", "_review.csv", "_note.csv")

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        """Whether the upload is a zip holding at least one recognised CSV."""
        try:
            with zipfile.ZipFile(uploaded_file) as zipref:
                return any(
                    name.endswith(cls.CsvFileSuffixes) for name in zipref.namelist()
                )
        except Exception as e:
            logger.error(
                f"unable to validate zip file {uploaded_file}",
                extra={"exception": e},
            )
        return False

    @property
    def overwrite(self) -> bool:
        """Whether existing marks and reviews are replaced rather than kept."""
        return self.metadata.get("mode", self.MERGE) == self.OVERWRITE

    def should_skip_existing_mark(
        self, mark: Mark, created_time: datetime.datetime
    ) -> bool:
        if self.overwrite:
            return False
        return super().should_skip_existing_mark(mark, created_time)

    def should_skip_existing_review(
        self,
        existing_review: Optional[Review],
        created_time: Optional[datetime.datetime],
    ) -> bool:
        if self.overwrite:
            return False
        return super().should_skip_existing_review(existing_review, created_time)
