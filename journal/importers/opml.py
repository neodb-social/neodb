import logging
import os
import re
from datetime import datetime

import django_rq
import listparser
import openpyxl
import pytz
from django.conf import settings
from django.core.files import uploadedfile
from markdownify import markdownify as md
from user_messages import api as msg

from catalog.common import *
from catalog.common.downloaders import *
from catalog.sites.rss import RSS
from common.utils import GenerateDateUUIDMediaFilePath
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
        print(f"{self.user} import opml start")
        skip = 0
        collection = None
        if self.mode == 1:
            collection = Collection.objects.create(
                owner=self.user, title=f"{self.user.display_name}的播客订阅列表"
            )
        for feed in feeds:
            print(f"{self.user} import {feed.url}")
            res = RSS(feed.url).get_resource_ready()
            if not res:
                print(f"{self.user} feed error {feed.url}")
                continue
            item = res.item
            if self.mode == 0:
                mark = Mark(self.user, item)
                if mark.shelfmember:
                    print(f"{self.user} marked, skip {feed.url}")
                    skip += 1
                else:
                    mark.update(
                        ShelfType.PROGRESS, None, None, visibility=self.visibility
                    )
            elif self.mode == 1 and collection:
                collection.append_item(item)
        print(f"{self.user} import opml end")
        msg.success(
            self.user,
            f"OPML导入完成，共处理{len(feeds)}篇，已存在{skip}篇。",
        )
