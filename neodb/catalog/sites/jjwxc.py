import html

from catalog.common import *
from catalog.models import *


@SiteManager.register
class JJWXC(AbstractSite):
    SITE_NAME = SiteName.JJWXC
    ID_TYPE = IdType.JJWXC
    URL_PATTERNS = [
        r"https://www\.jjwxc\.net/onebook\.php\?novelid=(\d+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.jjwxc.net/onebook.php?novelid={id_value}"

    def scrape(self):
        api_url = (
            f"https://app.jjwxc.net/androidapi/novelbasicinfo?novelId={self.id_value}"
        )
        o = BasicDownloader(api_url).download().json()
        return ResourceContent(
            metadata={
                "localized_title": [{"lang": "zh-cn", "text": o["novelName"]}],
                "author": [o["authorName"]],
                "format": Edition.BookFormat.WEB,
                "localized_description": [
                    {
                        "lang": "zh-cn",
                        "text": html.unescape(o["novelIntro"]).replace("<br/>", "\n"),
                    }
                ],
                "cover_image_url": o["novelCover"],
            },
        )
