import logging

import dateparser

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language
from common.models.misc import uniq

from .douban import DoubanDownloader, DoubanSearcher


@SiteManager.register
class DoubanGame(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanGame
    URL_PATTERNS = [
        r"\w+://www\.douban\.com/game/(\d+)/{0,1}",
        r"\w+://m.douban.com/game/subject/(\d+)/{0,1}",
        r"\w+://www.douban.com/doubanapp/dispatch\?uri=/game/(\d+)/",
        r"\w+://www.douban.com/doubanapp/dispatch/game/(\d+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.douban.com/game/" + id_value + "/"

    def scrape(self):
        content = DoubanDownloader(self.url).download().html()

        elem = self.query_list(content, "//div[@id='content']/h1/text()")
        title = elem[0].strip() if len(elem) else None
        if not title:
            raise ParseError(self, "title")

        elem = self.query_list(content, "//div[@id='comments']//h2/text()")
        title2 = elem[0].strip() if len(elem) else ""
        if title2:
            sp = title2.strip().rsplit("的短评", 1)
            title2 = sp[0] if len(sp) > 1 else ""
        if title2 and title.startswith(title2):
            orig_title = title[len(title2) :].strip()
            title = title2
        else:
            orig_title = ""

        other_title_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='别名:']/following-sibling::dd[1]/text()",
        )
        other_title = (
            other_title_elem[0].strip().split(" / ") if other_title_elem else []
        )

        developer_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='开发商:']/following-sibling::dd[1]/text()",
        )
        developer = developer_elem[0].strip().split(" / ") if developer_elem else None

        publisher_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='发行商:']/following-sibling::dd[1]/text()",
        )
        publisher = publisher_elem[0].strip().split(" / ") if publisher_elem else None

        platform_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='平台:']/following-sibling::dd[1]/a/text()",
        )
        platform = platform_elem if platform_elem else None

        genre_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='类型:']/following-sibling::dd[1]/a/text()",
        )
        genre = None
        if genre_elem:
            genre = [g for g in genre_elem if g != "游戏"]

        date_elem = self.query_list(
            content,
            "//dl[@class='thing-attr']//dt[text()='发行日期:']/following-sibling::dd[1]/text()",
        )
        release_date = dateparser.parse(date_elem[0].strip()) if date_elem else None
        release_date = release_date.strftime("%Y-%m-%d") if release_date else None

        brief_elem = self.query_list(content, "//div[@class='mod item-desc']/p/text()")
        brief = "\n".join(brief_elem) if brief_elem else ""

        img_url_elem = self.query_list(
            content, "//div[@class='item-subject-info']/div[@class='pic']//img/@src"
        )
        img_url = img_url_elem[0].strip() if img_url_elem else None

        titles = uniq([title] + other_title + ([orig_title] if orig_title else []))
        localized_title = [{"lang": detect_language(t), "text": t} for t in titles]
        localized_desc = (
            [{"lang": detect_language(brief), "text": brief}] if brief else []
        )

        pd = ResourceContent(
            metadata={
                "localized_title": localized_title,
                "localized_description": localized_desc,
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
