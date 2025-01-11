from catalog.common import *
from catalog.models import *


@SiteManager.register
class Qidian(AbstractSite):
    SITE_NAME = SiteName.Qidian
    ID_TYPE = IdType.Qidian
    URL_PATTERNS = [
        r"https://www\.qidian\.com/book/(\d+)",
        r"https://book\.qidian\.com/info/(\d+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://book.qidian.com/info/{id_value}/"

    def scrape(self):
        content = ProxiedDownloader(self.url).download().html()
        title_elem = content.xpath('//*[@id="bookName"]/text()')
        title = (
            title_elem[0].strip()  # type:ignore
            if title_elem
            else f"Unknown Title {self.id_value}"
        )

        brief_elem = content.xpath(
            "/html/body/div[1]/div[5]/div[3]/div[1]/div/div[1]/div[1]/p/text()"
        )
        brief = (
            "\n".join(p.strip() for p in brief_elem)  # type:ignore
            if brief_elem
            else None
        )

        img_url = f"https://bookcover.yuewen.com/qdbimg/349573/{self.id_value}"

        author_elem = content.xpath(
            "/html/body/div[1]/div[5]/div[1]/div[2]/h1/span[1]/a/text()"
        )
        authors = [author_elem[0].strip()] if author_elem else None  # type:ignore

        return ResourceContent(
            metadata={
                "localized_title": [{"lang": "zh-cn", "text": title}],
                "author": authors,
                "format": Edition.BookFormat.WEB,
                "localized_description": [{"lang": "zh-cn", "text": brief}],
                "cover_image_url": img_url,
            }
        )
