import csv
import tempfile
import zipfile

import pytz
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from loguru import logger
from markdownify import markdownify as md

from catalog.common import *
from catalog.common.downloaders import *
from catalog.models import *
from journal.models import *
from users.models import *

_tz_sh = pytz.timezone("Asia/Shanghai")


class LetterboxdImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "failed_urls": [],
        "file": None,
    }

    def get_item_by_url(self, url):
        try:
            h = BasicDownloader(url).download().html()
            tu = h.xpath("//a[@data-track-action='TMDb']/@href")
            iu = h.xpath("//a[@data-track-action='IMDb']/@href")
            if not tu:
                i = h.xpath('//span[@class="film-title-wrapper"]/a/@href')
                u2 = "https://letterboxd.com" + i[0]  # type:ignore
                h = BasicDownloader(u2).download().html()
                tu = h.xpath("//a[@data-track-action='TMDb']/@href")
                iu = h.xpath("//a[@data-track-action='IMDb']/@href")
            if not tu:
                logger.error(f"Unknown TMDB for {url}")
                return None
            site = SiteManager.get_site_by_url(tu[0])  # type:ignore
            if not site:
                return None
            if site.ID_TYPE == IdType.TMDB_TV:
                site = SiteManager.get_site_by_url(f"{site.url}/season/1")
                if not site:
                    return None
            try:
                site.get_resource_ready()
                return site.get_item()
            except Exception:
                imdb_url = str(iu[0])  # type:ignore
                logger.warning(
                    f"Fetching {url}: TMDB {site.url} failed, try IMDB {imdb_url}"
                )
                site = SiteManager.get_site_by_url(imdb_url)
                if not site:
                    return None
                site.get_resource_ready()
                return site.get_item()
        except Exception as e:
            logger.error(f"Fetching {url}: error {e}")

    def mark(self, url, shelf_type, date, rating=None, text=None, tags=None):
        item = self.get_item_by_url(url)
        if not item:
            logger.error(f"Unable to get item for {url}")
            self.progress(-1, url)
            return
        owner = self.user.identity
        mark = Mark(owner, item)
        if (
            mark.shelf_type == shelf_type
            or mark.shelf_type == ShelfType.COMPLETE
            or (
                mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
                and shelf_type == ShelfType.WISHLIST
            )
        ):
            self.progress(0)
            return
        visibility = self.metadata["visibility"]
        shelf_time_offset = {
            ShelfType.WISHLIST: " 20:00:00",
            ShelfType.PROGRESS: " 21:00:00",
            ShelfType.COMPLETE: " 22:00:00",
        }
        dt = parse_datetime(date + shelf_time_offset[shelf_type])
        if dt:
            dt = dt.replace(tzinfo=_tz_sh)
        rating_grade = round(float(rating) * 2) if rating else None
        comment = None
        if text:
            text = md(text)
            if len(text) < 360:
                comment = text
            else:
                title = _("a review of {item_title}").format(item_title=item.title)
                Review.update_item_review(item, owner, title, text, visibility, dt)
        tag_titles = [s.strip() for s in tags.split(",")] if tags else None
        mark.update(
            shelf_type,
            comment_text=comment,
            rating_grade=rating_grade,
            tags=tag_titles,
            visibility=visibility,
            created_time=dt,
        )
        self.progress(1)

    def progress(self, mark_state: int, url=None):
        self.metadata["total"] += 1
        self.metadata["processed"] += 1
        match mark_state:
            case 1:
                self.metadata["imported"] += 1
            case 0:
                self.metadata["skipped"] += 1
            case _:
                self.metadata["failed"] += 1
                if url:
                    self.metadata["failed_urls"].append(url)
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
