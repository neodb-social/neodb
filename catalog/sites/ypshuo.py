import json

from catalog.common import *
from catalog.models import *


@SiteManager.register
class Ypshuo(AbstractSite):
    SITE_NAME = SiteName.Ypshuo
    ID_TYPE = IdType.Ypshuo
    URL_PATTERNS = [
        r"https://www\.ypshuo\.com/novel/(\d+)\.html",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.ypshuo.com/novel/{id_value}.html"

    def scrape(self):
        api_url = f"https://www.ypshuo.com/api/novel/getInfo?novelId={self.id_value}"
        o = BasicDownloader(api_url).download().json()
        source = json.loads(o["data"]["source"])
        lookup_ids = {}
        for site in source:
            if site["siteName"] == "起点中文网":
                lookup_ids[IdType.Qidian] = site["bookId"]
        return ResourceContent(
            metadata={
                "localized_title": [{"lang": "zh-cn", "text": o["data"]["novel_name"]}],
                "author": [o["data"]["author_name"]],
                "format": Edition.BookFormat.WEB,
                "localized_description": [
                    {"lang": "zh-cn", "text": o["data"]["synopsis"]}
                ],
                "cover_image_url": o["data"]["novel_img"],
            },
            lookup_ids=lookup_ids,
        )
