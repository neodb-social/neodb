"""
Apple Music.

Scraping the website directly.

- Why not using Apple Music API?
- It requires Apple Developer Membership ($99 per year) to obtain a token.

"""

import json
from datetime import timedelta

from django.utils.dateparse import parse_duration
from loguru import logger

from catalog.common import *
from catalog.models import *
from common.models.lang import (
    SITE_DEFAULT_LANGUAGE,
    SITE_PREFERRED_LANGUAGES,
)
from common.models.misc import uniq

from .douban import *


@SiteManager.register
class AppleMusic(AbstractSite):
    SITE_NAME = SiteName.AppleMusic
    ID_TYPE = IdType.AppleMusic
    URL_PATTERNS = [
        r"https://music\.apple\.com/[a-z]{2}/album/[\w%-]+/(\d+)",
        r"https://music\.apple\.com/[a-z]{2}/album/(\d+)",
        r"https://music\.apple\.com/album/(\d+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://music.apple.com/album/{id_value}"

    def get_locales(self):
        locales = {}
        for lang in SITE_PREFERRED_LANGUAGES:
            match lang:
                case "zh":
                    locales.update({"zh": ["cn", "tw", "hk", "sg"]})
                case "en":
                    locales.update({"en": ["us", "gb", "ca"]})
                case "ja":
                    locales.update({"ja": ["jp"]})
                case "ko":
                    locales.update({"ko": ["kr"]})
                case "fr":
                    locales.update({"fr": ["fr", "ca"]})
        if not locales:
            locales = {"en": ["us"]}
        return locales

    def scrape(self):
        matched_schema_data = None
        localized_title = []
        localized_desc = []
        for lang, locales in self.get_locales().items():
            for loc in locales:  # waterfall thru all locales
                url = f"https://music.apple.com/{loc}/album/{self.id_value}"
                try:
                    tl = f"{lang}-{loc}" if lang == "zh" else lang
                    headers = {
                        "Accept-Language": tl,
                    }
                    headers.update(self.headers)
                    content = (
                        BasicDownloader(url, headers=self.headers).download().html()
                    )
                    logger.debug(f"got localized content from {url}")
                    txt: str = content.xpath(
                        "//script[@id='schema:music-album']/text()"
                    )[0]  # type:ignore
                    schema_data = json.loads(txt)
                    title = schema_data["name"]
                    if title:
                        localized_title.append({"lang": tl, "text": title})
                    try:
                        txt: str = content.xpath(
                            "//script[@id='serialized-server-data']/text()"
                        )[0]  # type:ignore
                        server_data = json.loads(txt)
                        brief = server_data[0]["data"]["sections"][0]["items"][0][
                            "modalPresentationDescriptor"
                        ]["paragraphText"]
                        if brief:
                            localized_desc.append({"lang": tl, "text": brief})
                    except Exception:
                        server_data = brief = None
                    if lang == SITE_DEFAULT_LANGUAGE or not matched_schema_data:
                        matched_schema_data = schema_data
                    break
                except Exception:
                    pass
        if matched_schema_data is None:  # no schema data found
            raise ParseError(self, f"localized content for {self.url}")
        artist = [a["name"] for a in matched_schema_data.get("byArtist", [])]
        release_date = matched_schema_data.get("datePublished", None)
        genre = matched_schema_data.get("genre", [])
        image_url = matched_schema_data.get("image", None)
        track_list = [t["name"] for t in matched_schema_data.get("tracks", [])]
        duration = round(
            sum(
                (parse_duration(t["duration"]) or timedelta()).total_seconds() * 1000
                for t in matched_schema_data.get("tracks", [])
            )
        )
        pd = ResourceContent(
            metadata={
                "localized_title": uniq(localized_title),
                "localized_description": uniq(localized_desc),
                "artist": artist,
                "genre": genre,
                "release_date": release_date,
                "track_list": "\n".join(track_list),
                "duration": duration,
                "cover_image_url": image_url,
            }
        )
        return pd
