"""
BoardGameGeek

ref: https://boardgamegeek.com/wiki/page/BGG_XML_API2
"""

import html

from catalog.common import *
from catalog.game.models import GameReleaseType
from catalog.models import *
from common.models.lang import detect_language


@SiteManager.register
class BoardGameGeek(AbstractSite):
    SITE_NAME = SiteName.BGG
    ID_TYPE = IdType.BGG
    URL_PATTERNS = [
        r"^\w+://boardgamegeek\.com/boardgame/(\d+)",
        r"^\w+://boardgamegeek\.com/boardgameexpansion/(\d+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://boardgamegeek.com/boardgame/" + id_value

    def scrape(self):
        api_url = f"https://boardgamegeek.com/xmlapi2/thing?stats=1&type=boardgame,boardgameexpansion&id={self.id_value}"
        content = BasicDownloader(api_url).download().xml()
        items = list(content.xpath("/items/item"))  # type: ignore
        if not len(items):
            raise ParseError(scraper=self, field="id")
        item = items[0]
        typ = self.query_str(item, "@type")
        title = self.query_str(item, "name[@type='primary']/@value")
        other_title = self.query_list(item, "name[@type='alternate']/@value")
        localized_title = [
            {"lang": detect_language(t), "text": t} for t in [title] + other_title
        ]
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
                "localized_title": localized_title,
                "localized_description": (
                    [{"lang": "en", "text": brief}] if brief else []
                ),
                "title": title,
                "other_title": other_title,
                "genre": category,
                "developer": developer,
                "publisher": publisher,
                "designer": designer,
                "artist": artist,
                "release_year": year,
                "release_type": (
                    GameReleaseType.EXPANSION
                    if typ == "boardgameexpansion"
                    else GameReleaseType.GAME
                ),
                "platform": ["Boardgame"],
                "brief": brief,
                # "official_site": official_site,
                "cover_image_url": cover_image_url,
            }
        )
        return pd
