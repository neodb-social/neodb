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

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://music.apple.com/album/{id_value}"

    def get_localized_urls(self):
        return [
            f"https://music.apple.com/{locale}/album/{self.id_value}"
            for locale in ["hk", "tw", "us", "sg", "cn", "gb", "ca", "fr"]
        ]

    def scrape(self):
        content = None
        # it's less than ideal to waterfall thru locales, a better solution
        # would be change ExternalResource to store preferred locale,
        # or to find an AppleMusic API to get available locales for an album
        for url in self.get_localized_urls():
            try:
                content = BasicDownloader(url).download().html()
                _logger.info(f"got localized content from {url}")
                break
            except Exception:
                pass
        if content is None:
            raise ParseError(self, f"localized content for {self.url}")
        elem = content.xpath("//script[@id='serialized-server-data']/text()")
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

        images = (
            content.xpath("//source[@type='image/jpeg']/@srcset") if content else []
        )
        image_elem: str = images[0] if images else ""  # type:ignore
        image_url = image_elem.split(" ")[0] if image_elem else None

        pd = ResourceContent(
            metadata={
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
        if pd.metadata["cover_image_url"]:
            imgdl = BasicImageDownloader(pd.metadata["cover_image_url"], self.url)
            try:
                pd.cover_image = imgdl.download().content
                pd.cover_image_extention = imgdl.extention
            except Exception:
                _logger.debug(
                    f'failed to download cover for {self.url} from {pd.metadata["cover_image_url"]}'
                )
        return pd
