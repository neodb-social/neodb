import listparser
from django.utils.translation import gettext as _
from loguru import logger

from catalog.common import *
from catalog.common.downloaders import *
from catalog.sites.rss import RSS
from journal.models import *
from users.models.task import Task


class OPMLImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "mode": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "failed_urls": [],
        "file": None,
    }

    @classmethod
    def validate_file(cls, f):
        try:
            return bool(listparser.parse(f.read()).feeds)
        except Exception:
            return False

    def run(self):
        with open(self.metadata["file"], "r") as f:
            feeds = listparser.parse(f.read()).feeds
            self.metadata["total"] = len(feeds)
            self.message = f"Processing {self.metadata['total']} feeds."
            self.save(update_fields=["metadata", "message"])

            collection = None
            if self.metadata["mode"] == 1:
                title = _("{username}'s podcast subscriptions").format(
                    username=self.user.display_name
                )
                collection = Collection.objects.create(
                    owner=self.user.identity,
                    title=title,
                    visibility=self.metadata["visibility"],
                )
            for feed in feeds:
                logger.info(f"{self.user} import {feed.url}")
                try:
                    res = RSS(feed.url).get_resource_ready()
                except Exception:
                    res = None
                if not res or not res.item:
                    logger.warning(f"{self.user} feed error {feed.url}")
                    self.metadata["failed"] += 1
                    continue
                item = res.item
                if self.metadata["mode"] == 0:
                    mark = Mark(self.user.identity, item)
                    if mark.shelfmember:
                        logger.info(f"{self.user} marked, skip {feed.url}")
                        self.metadata["skipped"] += 1
                    else:
                        self.metadata["imported"] += 1
                        mark.update(
                            ShelfType.PROGRESS,
                            None,
                            None,
                            visibility=self.metadata["visibility"],
                        )
                elif self.metadata["mode"] == 1 and collection:
                    self.metadata["imported"] += 1
                    collection.append_item(item)
                self.metadata["processed"] += 1
                self.save(update_fields=["metadata"])
        self.message = f"{self.metadata['imported']} feeds imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed."
        self.save(update_fields=["message"])
