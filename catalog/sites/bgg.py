"""
BoardGameGeek

ref: https://boardgamegeek.com/wiki/page/BGG_XML_API2
"""
import html

from langdetect import detect
from loguru import logger

from catalog.common import *
from catalog.models import *


@SiteManager.register
class BoardGameGeek(AbstractSite):
    SITE_NAME = SiteName.BGG
    ID_TYPE = IdType.BGG
    URL_PATTERNS = [
        r"^\w+://boardgamegeek\.com/boardgame/(\d+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://boardgamegeek.com/boardgame/" + id_value

    def scrape(self):
        api_url = f"https://boardgamegeek.com/xmlapi2/thing?stats=1&type=boardgame&id={self.id_value}"
        content = BasicDownloader(api_url).download().xml()
        items = list(content.xpath("/items/item"))  # type: ignore
        if not len(items):
            raise ParseError("boardgame not found", field="id")
        item = items[0]
        title = self.query_str(item, "name[@type='primary']/@value")
        other_title = self.query_list(item, "name[@type='alternate']/@value")
        zh_title = [
            t for t in other_title if detect(t) in ["zh", "jp", "ko", "zh-cn", "zh-tw"]
        ]
        if zh_title:
            for z in zh_title:
                other_title.remove(z)
            other_title = zh_title + other_title

        cover_image_url = self.query_str(item, "image/text()")
        brief = html.unescape(self.query_str(item, "description/text()"))
        year = self.query_str(item, "yearpublished/@value")
        designer = self.query_list(item, "link[@type='boardgamedesigner']/@value")
        artist = self.query_list(item, "link[@type='boardgameartist']/@value")
        publisher = self.query_list(item, "link[@type='boardgamepublisher']/@value")
        developer = self.query_list(item, "link[@type='boardgamedeveloper']/@value")
        category = self.query_list(item, "link[@type='boardgamecategory']/@value")

        pd = ResourceContent(
            metadata={
                "title": title,
                "other_title": other_title,
                "genre": category,
                "developer": developer,
                "publisher": publisher,
                "designer": designer,
                "artist": artist,
                "release_year": year,
                "platform": ["Boardgame"],
                "brief": brief,
                # "official_site": official_site,
                "cover_image_url": cover_image_url,
            }
        )
        if pd.metadata["cover_image_url"]:
            imgdl = BasicImageDownloader(pd.metadata["cover_image_url"], self.url)
            try:
                pd.cover_image = imgdl.download().content
                pd.cover_image_extention = imgdl.extention
            except Exception:
                logger.debug(
                    f'failed to download cover for {self.url} from {pd.metadata["cover_image_url"]}'
                )
        return pd
