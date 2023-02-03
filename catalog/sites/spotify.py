"""
Spotify
"""
from django.conf import settings
from catalog.common import *
from catalog.models import *
from catalog.music.utils import upc_to_gtin_13
from .douban import *
import time
import datetime
import requests
import dateparser
import logging


_logger = logging.getLogger(__name__)


spotify_token = None
spotify_token_expire_time = time.time()


@SiteManager.register
class Spotify(AbstractSite):
    SITE_NAME = SiteName.Spotify
    ID_TYPE = IdType.Spotify_Album
    URL_PATTERNS = [r"\w+://open\.spotify\.com/album/([a-zA-Z0-9]+)"]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://open.spotify.com/album/{id_value}"

    def scrape(self):
        api_url = f"https://api.spotify.com/v1/albums/{self.id_value}"
        headers = {"Authorization": f"Bearer {get_spotify_token()}"}
        res_data = BasicDownloader(api_url, headers=headers).download().json()
        artist = []
        for artist_dict in res_data["artists"]:
            artist.append(artist_dict["name"])

        title = res_data["name"]

        genre = ", ".join(res_data["genres"])

        company = []
        for com in res_data["copyrights"]:
            company.append(com["text"])

        duration = 0
        track_list = []
        track_urls = []
        for track in res_data["tracks"]["items"]:
            track_urls.append(track["external_urls"]["spotify"])
            duration += track["duration_ms"]
            if res_data["tracks"]["items"][-1]["disc_number"] > 1:
                # more than one disc
                track_list.append(
                    str(track["disc_number"])
                    + "-"
                    + str(track["track_number"])
                    + ". "
                    + track["name"]
                )
            else:
                track_list.append(str(track["track_number"]) + ". " + track["name"])
        track_list = "\n".join(track_list)

        release_date = dateparser.parse(res_data["release_date"]).strftime("%Y-%m-%d")

        gtin = None
        if res_data["external_ids"].get("upc"):
            gtin = upc_to_gtin_13(res_data["external_ids"].get("upc"))
        if res_data["external_ids"].get("ean"):
            gtin = upc_to_gtin_13(res_data["external_ids"].get("ean"))
        isrc = None
        if res_data["external_ids"].get("isrc"):
            isrc = res_data["external_ids"].get("isrc")

        pd = ResourceContent(
            metadata={
                "title": title,
                "artist": artist,
                "genre": genre,
                "track_list": track_list,
                "release_date": release_date,
                "duration": duration,
                "company": company,
                "brief": None,
                "cover_image_url": res_data["images"][0]["url"],
            }
        )
        if gtin:
            pd.lookup_ids[IdType.GTIN] = gtin
        if isrc:
            pd.lookup_ids[IdType.ISRC] = isrc
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


def get_spotify_token():
    global spotify_token, spotify_token_expire_time
    if get_mock_mode():
        return "mocked"
    if spotify_token is None or is_spotify_token_expired():
        invoke_spotify_token()
    return spotify_token


def is_spotify_token_expired():
    global spotify_token_expire_time
    return True if spotify_token_expire_time <= time.time() else False


def invoke_spotify_token():
    global spotify_token, spotify_token_expire_time
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {settings.SPOTIFY_CREDENTIAL}"},
    )
    data = r.json()
    if r.status_code == 401:
        # token expired, try one more time
        # this maybe caused by external operations,
        # for example debugging using a http client
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {settings.SPOTIFY_CREDENTIAL}"},
        )
        data = r.json()
    elif r.status_code != 200:
        raise Exception(f"Request to spotify API fails. Reason: {r.reason}")
    # minus 2 for execution time error
    spotify_token_expire_time = int(data["expires_in"]) + time.time() - 2
    spotify_token = data["access_token"]
