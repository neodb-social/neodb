"""
Apple Music.

Scraping the website directly.

- Why not using Apple Music API?
- It requires Apple Developer Membership ($99 per year) to obtain a token.

"""
from catalog.common import *
from catalog.models import *
from .douban import *
import json
import logging
import dateparser


_logger = logging.getLogger(__name__)


@SiteManager.register
class AppleMusic(AbstractSite):
    SITE_NAME = SiteName.AppleMusic
    ID_TYPE = IdType.AppleMusic
    URL_PATTERNS = [r"https://music\.apple\.com/[a-z]{2}/album/[\d\w%-]+/(\d+)[^\d]*"]
    DOMAIN_PATTERNS = [
        r"(https://music\.apple\.com/[a-z]{2})/album/[\d\w%-]+/\d+[^\d]*"
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album

    @classmethod
    def url_to_id(cls, url: str):
        """
        Transform url to id. Find the domain of the provided url.
        """
        domain = next(
            iter([re.match(p, url) for p in cls.DOMAIN_PATTERNS if re.match(p, url)]),
            None,
        )
        cls.domain = domain[1] if domain else None
        u = next(
            iter([re.match(p, url) for p in cls.URL_PATTERNS if re.match(p, url)]),
            None,
        )
        return u[1] if u else None

    @classmethod
    def id_to_url(cls, id_value):
        # find albums according to the domain of the provided link
        return f"{cls.domain}/album/{id_value}"

    def scrape(self):
        content = BasicDownloader(self.url).download().html()
        elem = content.xpath("//script[@id='serialized-server-data']/text()")
        page_data = json.loads(elem[0])[0]
        album_data = page_data["data"]["sections"][0]["items"][0]
        title = album_data["title"]
        artist_list = album_data["subtitleLinks"]
        artist = [item["title"] for item in artist_list]

        track_data = page_data["data"]["seoData"]
        date_elem = track_data.get("musicReleaseDate")
        release_date = (
            dateparser.parse(date_elem.strip()).strftime("%Y-%m-%d")
            if date_elem
            else None
        )
        track_list = [track["attributes"]["name"] for track in track_data["ogSongs"]]
        duration_list = [
            track["attributes"].get("durationInMillis")
            for track in track_data["ogSongs"]
        ]
        duration = int(sum(duration_list))
        genre = track_data["schemaContent"].get("genre")
        if genre:
            genre = [
                genre[0]
            ]  # apple treat "Music" as a genre. Thus, only the first genre is obtained.

        image_elem = content.xpath("//source[@type='image/jpeg']/@srcset")[0]
        image_url = image_elem.split(" ")[0] if image_elem else None

        pd = ResourceContent(
            metadata={
                "title": title,
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
