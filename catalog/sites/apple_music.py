"""
Apple Music.

Scraping the website directly.

- Why not using Apple Music API?
- It requires Apple Developer Membership ($99 per year) to obtain a token.

"""

import json
import logging

import dateparser

from catalog.common import *
from catalog.models import *
from common.models.lang import (
    SITE_DEFAULT_LANGUAGE,
    SITE_PREFERRED_LANGUAGES,
    detect_language,
)
from common.models.misc import uniq

from .douban import *

_logger = logging.getLogger(__name__)


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
        "Accept-Language": BasicDownloader.get_accept_language(),
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
        matched_content = None
        localized_title = []
        localized_desc = []
        for lang, locales in self.get_locales().items():
            for loc in locales:  # waterfall thru all locales
                url = f"https://music.apple.com/{loc}/album/{self.id_value}"
                try:
                    content = (
                        BasicDownloader(url, headers=self.headers).download().html()
                    )
                    _logger.info(f"got localized content from {url}")
                    elem = content.xpath(
                        "//script[@id='serialized-server-data']/text()"
                    )
                    txt: str = elem[0]  # type:ignore
                    page_data = json.loads(txt)[0]
                    album_data = page_data["data"]["sections"][0]["items"][0]
                    title = album_data["title"]
                    brief = album_data.get("modalPresentationDescriptor", {}).get(
                        "paragraphText", ""
                    )
                    tl = detect_language(title + " " + brief)
                    localized_title.append({"lang": tl, "text": title})
                    if brief:
                        localized_desc.append({"lang": tl, "text": brief})
                    if lang == SITE_DEFAULT_LANGUAGE or not matched_content:
                        matched_content = content
                    break
                except Exception:
                    pass
        if matched_content is None:
            raise ParseError(self, f"localized content for {self.url}")
        elem = matched_content.xpath("//script[@id='serialized-server-data']/text()")
        txt: str = elem[0]  # type:ignore
        page_data = json.loads(txt)[0]
        album_data = page_data["data"]["sections"][0]["items"][0]
        title = album_data["title"]
        brief = album_data.get("modalPresentationDescriptor")
        brief = brief.get("paragraphText") if brief else None
        artist_list = album_data["subtitleLinks"]
        artist = [item["title"] for item in artist_list]

        track_data = page_data["data"]["seoData"]
        date_elem = track_data.get("musicReleaseDate")
        release_datetime = dateparser.parse(date_elem.strip()) if date_elem else None
        release_date = (
            release_datetime.strftime("%Y-%m-%d") if release_datetime else None
        )

        track_list = [
            f"{i}. {track['attributes']['name']}"
            for i, track in enumerate(track_data["ogSongs"], 1)
        ]
        duration_list = [
            track["attributes"].get("durationInMillis", 0)
            for track in track_data["ogSongs"]
        ]
        duration = int(sum(duration_list))
        genre = track_data["schemaContent"].get("genre")
        if genre:
            genre = [
                genre[0]
            ]  # apple treat "Music" as a genre. Thus, only the first genre is obtained.

        images = matched_content.xpath("//source[@type='image/jpeg']/@srcset")
        image_elem: str = images[0] if images else ""  # type:ignore
        image_url = image_elem.split(" ")[0] if image_elem else None

        pd = ResourceContent(
            metadata={
                "localized_title": uniq(localized_title),
                "localized_description": uniq(localized_desc),
                "title": title,
                "brief": brief,
                "artist": artist,
                "genre": genre,
                "release_date": release_date,
                "track_list": "\n".join(track_list),
                "duration": duration,
                "cover_image_url": image_url,
            }
        )
        return pd
