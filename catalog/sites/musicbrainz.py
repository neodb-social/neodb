"""
MusicBrainz

Using MusicBrainz API and Cover Art Archive API
"""
import logging

from django.conf import settings

from catalog.common import *
from catalog.models import *
from catalog.music.utils import upc_to_gtin_13

from .douban import *

_logger = logging.getLogger(__name__)


@SiteManager.register
class MusicBrainz(AbstractSite):
    SITE_NAME = SiteName.MusicBrainz
    ID_TYPE = IdType.MusicBrainz
    URL_PATTERNS = [
        r"https://musicbrainz\.org/release/([\w\-]+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album
    headers = {"User-Agent": "Neodb"}

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://musicbrainz.org/release/{id_value}"

    def scrape(self):
        data = self._get_musicbrainz_data()
        title = data.get("title")
        artists = [artist.get("name") for artist in data.get("artist-credit")]

        # should i get label for company??
        # example: https://musicbrainz.org/release/1249b0d1-64b4-4e09-ab4b-e875746cc062
        # label: Open Road Recordings, Release copyrighted Big Machine Records.
        # it seems like only label available in API
        company = None

        media = self._get_media(data)
        disc_count, track_list = self._get_track_details(data)

        barcode = data.get("barcode")
        if barcode:
            barcode = upc_to_gtin_13(barcode)

        release_date = self._get_release_date(data)
        cover_image_available = data["cover-art-archive"]["front"]
        image_url = self._get_cover_image_url() if cover_image_available else ""
        pd = ResourceContent(
            metadata={
                "title": title,
                "artist": artists,
                "genre": None,  # genre is only available at artist or song level
                "track_list": "\n".join(track_list),
                "release_date": release_date,
                "company": company,
                "media": media,
                "disc_count": disc_count,
                "cover_image_url": image_url,
            }
        )
        if barcode:
            pd.lookup_ids[IdType.GTIN] = barcode
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

    def _get_musicbrainz_data(self):
        musicbrainz_api_url = f"https://musicbrainz.org/ws/2/release/{self.id_value}?inc=recordings+artists?fmt=json"
        data = (
            BasicDownloader(musicbrainz_api_url, headers=self.headers).download().json()
        )
        return data

    @staticmethod
    def _get_release_date(data):
        try:
            release_date = data["release-events"]["date"]
        except Exception:
            return ""
        return release_date

    @staticmethod
    def _get_media(data):
        media = [media["format"] for media in data["media"]]
        media = list(set(media))
        return "+".join(media)

    @staticmethod
    def _get_track_details(data) -> []:
        # track_list includes media type as first line
        track_list = []
        disc_count = 0
        include_media_type = False
        if len(data["media"]) > 1:
            include_media_type = True
        for media in data["media"]:
            if media["format"] != "Digital Media":
                disc_count += 1
            if include_media_type:
                track_list.append(f"{media['format']} {media['position']}")
            track_list += [track["title"] for track in media["tracks"]]
            track_list.append(" ")
        return disc_count, track_list

    def _get_cover_image_url(self) -> str:
        # caa: Cover Art Archive
        caa_api_url = f"http://coverartarchive.org/release/{self.id_value}"
        try:
            data = BasicDownloader(caa_api_url, headers=self.headers).download().json()
        except Exception:
            return ""

        for image in data.get("images"):
            if image["front"] is True:
                return image["thumbnails"].get("small")
        return ""
