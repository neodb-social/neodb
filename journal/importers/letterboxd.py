import csv
import re
import tempfile
import zipfile
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from loguru import logger
from user_messages import api as msg

from catalog.common import *
from catalog.common.downloaders import *
from catalog.models import *
from journal.models import *
from users.models import *


class LetterboxdImporter(Task):
    TaskQueue = "import"
    TaskType = "import.letterboxd"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "file": None,
    }

    def get_item_by_url(self, url):
        try:
            h = BasicDownloader(url).html()  # type:ignore
            tt = h.xpath("//body/@data-tmdb-type")[0].strip()
            ti = h.xpath("//body/@data-tmdb-type")[0].strip()
            if tt != "movie":
                logger.error(f"Unknown TMDB type {tt} / {ti}")
                return None
            site = SiteManager.get_site_by_id(IdType.TMDB_Movie, ti)
            if not site:
                return None
            site.get_resource_ready()
            return site.get_item()
        except Exception as e:
            logger.error(f"Unable to parse {url} {e}")

    def mark(self, url, shelf_type, date, rating=None, review=None, tags=None):
        item = self.get_item_by_url(url)
        if not item:
            logger.error(f"Unable to get item for {url}")
            return
        owner = self.user.identity
        mark = Mark(owner, item)
        if (
            mark.shelf_type == shelf_type
            or mark.shelf_type == ShelfType.COMPLETE
            or (
                mark.shelf_type == ShelfType.PROGRESS
                and shelf_type == ShelfType.WISHLIST
            )
        ):
            return
        visibility = self.metadata["visibility"]
        shelf_time_offset = {
            ShelfType.WISHLIST: " 20:00:00",
            ShelfType.PROGRESS: " 21:00:00",
            ShelfType.COMPLETE: " 22:00:00",
        }
        dt = parse_datetime(date + shelf_time_offset[shelf_type])
        mark.update(
            shelf_type,
            comment_text=review or None,
            rating_grade=round(rating * 2) if rating else None,
            visibility=visibility,
            created_time=dt,
        )
        if tags:
            tag_titles = [s.strip() for s in tags.split(",")]
            TagManager.tag_item(item, owner, tag_titles, visibility)

    def run(self):
        uris = set()
        filename = self.metadata["file"]
        with zipfile.ZipFile(filename, "r") as zipref:
            with tempfile.TemporaryDirectory() as tmpdirname:
                logger.debug(f"Extracting {filename} to {tmpdirname}")
                zipref.extractall(tmpdirname)
                with open(tmpdirname + "/reviews.csv") as f:
                    reader = csv.DictReader(f, delimiter=",")
                    for row in reader:
                        uris.add(row["Letterboxd URI"])
                        self.mark(
                            row["Letterboxd URI"],
                            ShelfType.COMPLETE,
                            row["Watched Date"],
                            row["Review"],
                            row["Rating"],
                            row["Tags"],
                        )
                with open(tmpdirname + "/ratings.csv") as f:
                    reader = csv.DictReader(f, delimiter=",")
                    for row in reader:
                        if row["Letterboxd URI"] in uris:
                            continue
                        uris.add(row["Letterboxd URI"])
                        self.mark(
                            row["Letterboxd URI"],
                            ShelfType.COMPLETE,
                            row["Date"],
                            row["Rating"],
                        )
                with open(tmpdirname + "/watched.csv") as f:
                    reader = csv.DictReader(f, delimiter=",")
                    for row in reader:
                        if row["Letterboxd URI"] in uris:
                            continue
                        uris.add(row["Letterboxd URI"])
                        self.mark(
                            row["Letterboxd URI"],
                            ShelfType.COMPLETE,
                            row["Date"],
                        )
                with open(tmpdirname + "/watchlist.csv") as f:
                    reader = csv.DictReader(f, delimiter=",")
                    for row in reader:
                        if row["Letterboxd URI"] in uris:
                            continue
                        uris.add(row["Letterboxd URI"])
                        self.mark(
                            row["Letterboxd URI"],
                            ShelfType.WISHLIST,
                            row["Date"],
                        )
