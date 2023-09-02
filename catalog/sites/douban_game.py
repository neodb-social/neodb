import logging

import dateparser

from catalog.common import *
from catalog.models import *

from .douban import DoubanDownloader

_logger = logging.getLogger(__name__)


@SiteManager.register
class DoubanGame(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanGame
    URL_PATTERNS = [
        r"\w+://www\.douban\.com/game/(\d+)/{0,1}",
        r"\w+://m.douban.com/game/subject/(\d+)/{0,1}",
        r"\w+://www.douban.com/doubanapp/dispatch\?uri=/game/(\d+)/",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(self, id_value):
        return "https://www.douban.com/game/" + id_value + "/"

    def scrape(self):
        content = DoubanDownloader(self.url).download().html()

        elem = content.xpath("//div[@id='content']/h1/text()")
        title = elem[0].strip() if len(elem) else None
        if not title:
            raise ParseError(self, "title")

        other_title_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='别名:']/following-sibling::dd[1]/text()"
        )
        other_title = (
            other_title_elem[0].strip().split(" / ") if other_title_elem else None
        )

        developer_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='开发商:']/following-sibling::dd[1]/text()"
        )
        developer = developer_elem[0].strip().split(" / ") if developer_elem else None

        publisher_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='发行商:']/following-sibling::dd[1]/text()"
        )
        publisher = publisher_elem[0].strip().split(" / ") if publisher_elem else None

        platform_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='平台:']/following-sibling::dd[1]/a/text()"
        )
        platform = platform_elem if platform_elem else None

        genre_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='类型:']/following-sibling::dd[1]/a/text()"
        )
        genre = None
        if genre_elem:
            genre = [g for g in genre_elem if g != "游戏"]

        date_elem = content.xpath(
            "//dl[@class='game-attr']//dt[text()='发行日期:']/following-sibling::dd[1]/text()"
        )
        release_date = (
            dateparser.parse(date_elem[0].strip()).strftime("%Y-%m-%d")
            if date_elem
            else None
        )

        brief_elem = content.xpath("//div[@class='mod item-desc']/p/text()")
        brief = "\n".join(brief_elem) if brief_elem else None

        img_url_elem = content.xpath(
            "//div[@class='item-subject-info']/div[@class='pic']//img/@src"
        )
        img_url = img_url_elem[0].strip() if img_url_elem else None

        pd = ResourceContent(
            metadata={
                "title": title,
                "other_title": other_title,
                "developer": developer,
                "publisher": publisher,
                "release_date": release_date,
                "genre": genre,
                "platform": platform,
                "brief": brief,
                "cover_image_url": img_url,
            }
        )
        if pd.metadata["cover_image_url"]:
            (
                pd.cover_image,
                pd.cover_image_extention,
            ) = BasicImageDownloader.download_image(
                pd.metadata["cover_image_url"], self.url
            )
        return pd
