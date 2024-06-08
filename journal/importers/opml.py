import django_rq
import listparser
from auditlog.context import set_actor
from django.utils.translation import gettext as _
from loguru import logger
from user_messages import api as msg

from catalog.common import *
from catalog.common.downloaders import *
from catalog.sites.rss import RSS
from journal.models import *


class OPMLImporter:
    def __init__(self, user, visibility, mode):
        self.user = user
        self.visibility = visibility
        self.mode = mode

    def parse_file(self, uploaded_file):
        return listparser.parse(uploaded_file.read()).feeds

    def import_from_file(self, uploaded_file):
        feeds = self.parse_file(uploaded_file)
        if not feeds:
            return False
        django_rq.get_queue("import").enqueue(self.import_from_file_task, feeds)
        return True

    def import_from_file_task(self, feeds):
        logger.info(f"{self.user} import opml start")
        skip = 0
        collection = None
        with set_actor(self.user):
            if self.mode == 1:
                title = _("{username}'s podcast subscriptions").format(
                    username=self.user.display_name
                )
                collection = Collection.objects.create(
                    owner=self.user.identity, title=title
                )
            for feed in feeds:
                logger.info(f"{self.user} import {feed.url}")
                try:
                    res = RSS(feed.url).get_resource_ready()
                except Exception:
                    res = None
                if not res or not res.item:
                    logger.warning(f"{self.user} feed error {feed.url}")
                    continue
                item = res.item
                if self.mode == 0:
                    mark = Mark(self.user.identity, item)
                    if mark.shelfmember:
                        logger.info(f"{self.user} marked, skip {feed.url}")
                        skip += 1
                    else:
                        mark.update(
                            ShelfType.PROGRESS, None, None, visibility=self.visibility
                        )
                elif self.mode == 1 and collection:
                    collection.append_item(item)
        logger.info(f"{self.user} import opml end")
        msg.success(
            self.user,
            f"OPML import complete, {len(feeds)} feeds processed, {skip} exisiting feeds skipped.",
        )
