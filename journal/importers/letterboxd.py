import csv
import tempfile
import zipfile

import pytz
from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.common import *
from catalog.common.downloaders import *
from catalog.models import *
from journal.models import *
from users.models import *

_tz_sh = pytz.timezone("Asia/Shanghai")


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

    class Meta:
        proxy = True

    def get_item_by_url(self, url):
        try:
            h = BasicDownloader(url).download().html()
            if not h.xpath("//body/@data-tmdb-type"):
                i = h.xpath('//span[@class="film-title-wrapper"]/a/@href')
                u2 = "https://letterboxd.com" + i[0]  # type:ignore
                h = BasicDownloader(u2).download().html()
            tt = h.xpath("//body/@data-tmdb-type")[0].strip()  # type:ignore
            ti = str(h.xpath("//body/@data-tmdb-id")[0].strip())  # type:ignore
            if tt != "movie" or not ti:
                logger.error(f"Unknown TMDB ({tt}/{ti}) for {url}")
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
            self.progress(-1)
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
            self.progress(0)
            return 0
        visibility = self.metadata["visibility"]
        shelf_time_offset = {
            ShelfType.WISHLIST: " 20:00:00",
            ShelfType.PROGRESS: " 21:00:00",
            ShelfType.COMPLETE: " 22:00:00",
        }
        dt = parse_datetime(date + shelf_time_offset[shelf_type])
        if dt:
            dt = dt.replace(tzinfo=_tz_sh)
        mark.update(
            shelf_type,
            comment_text=review or None,
            rating_grade=round(float(rating) * 2) if rating else None,
            visibility=visibility,
            created_time=dt,
        )
        if tags:
            tag_titles = [s.strip() for s in tags.split(",")]
            TagManager.tag_item(item, owner, tag_titles, visibility)
        self.progress(1)

    def progress(self, mark_state: int):
        self.metadata["total"] += 1
        self.metadata["processed"] += 1
        match mark_state:
            case 1:
                self.metadata["imported"] += 1
            case 0:
                self.metadata["skipped"] += 1
            case _:
                self.metadata["failed"] += 1
        self.message = f"{self.metadata['imported']} imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed"
        self.save(update_fields=["metadata", "message"])

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
                            row["Rating"],
                            row["Review"],
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
