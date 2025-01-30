"""
Spotify
"""

import logging
import time

import dateparser
import httpx
import requests
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.music.utils import upc_to_gtin_13
from common.models.lang import detect_language

from .douban import *

_logger = logging.getLogger(__name__)


spotify_token = None
spotify_token_expire_time = time.time()


@SiteManager.register
class Spotify(AbstractSite):
    SITE_NAME = SiteName.Spotify
    ID_TYPE = IdType.Spotify_Album
    URL_PATTERNS = [
        r"^\w+://open\.spotify\.com/album/([a-zA-Z0-9]+).*",
        r"^\w+://open\.spotify\.com/[\w\-]+/album/([a-zA-Z0-9]+).*",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://open.spotify.com/album/{id_value}"

    def scrape(self):
        api_url = f"https://api.spotify.com/v1/albums/{self.id_value}"
        headers = {
            "Authorization": f"Bearer {get_spotify_token()}",
            "User-Agent": settings.NEODB_USER_AGENT,
        }
        res_data = BasicDownloader(api_url, headers=headers).download().json()
        artist = []
        for artist_dict in res_data["artists"]:
            artist.append(artist_dict["name"])

        title = res_data["name"]

        genre = res_data.get("genres", [])

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
        dt = dateparser.parse(res_data["release_date"])
        release_date = dt.strftime("%Y-%m-%d") if dt else None

        gtin = None
        if res_data["external_ids"].get("upc"):
            gtin = upc_to_gtin_13(res_data["external_ids"].get("upc"))
        if res_data["external_ids"].get("ean"):
            gtin = upc_to_gtin_13(res_data["external_ids"].get("ean"))
        isrc = None
        if res_data["external_ids"].get("isrc"):
            isrc = res_data["external_ids"].get("isrc")
        lang = detect_language(title)
        pd = ResourceContent(
            metadata={
                "title": title,
                "localized_title": [{"lang": lang, "text": title}],
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
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["music", "all"]:
            return []
        results = []
        api_url = f"https://api.spotify.com/v1/search?q={q}&type=album&limit={page_size}&offset={page * page_size}"
        async with httpx.AsyncClient() as client:
            try:
                headers = {"Authorization": f"Bearer {get_spotify_token()}"}
                response = await client.get(api_url, headers=headers, timeout=2)
                j = response.json()
                if j.get("albums"):
                    for a in j["albums"]["items"]:
                        title = a["name"]
                        subtitle = a.get("release_date", "")
                        for artist in a.get("artists", []):
                            subtitle += " " + artist.get("name", "")
                        url = a["external_urls"]["spotify"]
                        cover = a["images"][0]["url"] if a.get("images") else ""
                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Music,
                                SiteName.Spotify,
                                url,
                                title,
                                subtitle,
                                "",
                                cover,
                            )
                        )
                else:
                    logger.warning(f"Spotify search '{q}' no results found.")
            except httpx.ReadTimeout:
                logger.warning("Spotify search timeout", extra={"query": q})
            except Exception as e:
                logger.error("Spotify search error", extra={"query": q, "exception": e})
        return results


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
