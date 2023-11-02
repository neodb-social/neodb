"""
Discogs.
"""
import json
import logging

import requests
from django.conf import settings

from catalog.common import *
from catalog.models import *
from catalog.music.utils import upc_to_gtin_13

from .douban import *

_logger = logging.getLogger(__name__)


@SiteManager.register
class DiscogsRelease(AbstractSite):
    SITE_NAME = SiteName.Discogs
    ID_TYPE = IdType.Discogs_Release
    URL_PATTERNS = [
        r"https://www\.discogs\.com/release/(\d+)[^\d]*",
        r"https://www\.discogs\.com/[a-z]{2}/release/(\d+)[^\d]*",
        r"https://www\.discogs\.com/[a-z]{2}_[A-Z]{2}/release/(\d+)[^\d]*",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.discogs.com/release/{id_value}"

    def scrape(self):
        release = get_discogs_data("releases", self.id_value)
        title = release.get("title")
        artist = [artist.get("name") for artist in release.get("artists")]
        genre = release.get("genres", [])
        track_list = [track.get("title") for track in release.get("tracklist")]
        company = list(
            set([company.get("name") for company in release.get("companies")])
        )

        media, disc_count = None, None
        formats = release.get("formats", [])
        if len(formats) == 1:
            media = formats[0].get("name")
            disc_count = formats[0].get("qty")

        identifiers = release.get("identifiers")
        barcode = None
        if identifiers:
            for i in identifiers:
                if i["type"] == "Barcode":
                    barcode = upc_to_gtin_13(
                        i["value"].replace(" ", "").replace("-", "")
                    )
        image_url = None
        if len(release.get("images", [])) > 0:
            image_url = release["images"][0].get("uri")
        pd = ResourceContent(
            metadata={
                "title": title,
                "artist": artist,
                "genre": genre,
                "track_list": "\n".join(track_list),
                "release_date": None,  # only year provided by API
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


@SiteManager.register
class DiscogsMaster(AbstractSite):
    SITE_NAME = SiteName.Discogs
    ID_TYPE = IdType.Discogs_Master
    URL_PATTERNS = [
        r"^https://www\.discogs\.com/master/(\d+)[^\d]*",
        r"^https://www\.discogs\.com/[\w\-]+/master/(\d+)[^\d]*",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.discogs.com/master/{id_value}"

    def scrape(self):
        master_release = get_discogs_data("masters", self.id_value)
        title = master_release.get("title")
        artist = [artist.get("name") for artist in master_release.get("artists")]
        genre = master_release.get("genres", [])
        track_list = [track.get("title") for track in master_release.get("tracklist")]

        image_url = None
        if len(master_release.get("images")) > 0:
            image_url = master_release["images"][0].get("uri")
        pd = ResourceContent(
            metadata={
                "title": title,
                "artist": artist,
                "genre": genre,
                "track_list": "\n".join(track_list),
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


def get_discogs_data(data_type: str, discogs_id):
    if data_type not in ("releases", "masters"):
        raise ValueError("data_type can only be in ('releases' or masters')")
    user_agent_string = "Neodb/0.1"
    user_token = settings.DISCOGS_API_KEY
    headers = {
        "User-Agent": user_agent_string,
        "Authorization": f"Discogs token={user_token}",
    }
    api_url = f"https://api.discogs.com/{data_type}/{discogs_id}"
    data = BasicDownloader(api_url, headers=headers).download().json()
    return data
