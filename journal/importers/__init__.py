import os
import zipfile

from .csv import CsvImporter
from .douban import DoubanImporter
from .goodreads import GoodreadsImporter
from .letterboxd import LetterboxdImporter
from .opml import OPMLImporter


def get_neodb_importer(filename: str) -> type[CsvImporter] | None:
    if not os.path.exists(filename) or not zipfile.is_zipfile(filename):
        return None
    with zipfile.ZipFile(filename, "r") as z:
        files = z.namelist()
        if any(f == "journal.ndjson" for f in files):
            return None
        if any(
            f.endswith("_mark.csv")
            or f.endswith("_review.csv")
            or f.endswith("_note.csv")
            for f in files
        ):
            return CsvImporter


__all__ = [
    "CsvImporter",
    "LetterboxdImporter",
    "OPMLImporter",
    "DoubanImporter",
    "GoodreadsImporter",
    "get_neodb_importer",
]
