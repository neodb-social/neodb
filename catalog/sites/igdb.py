"""
IGDB

use (e.g. "portal-2") as id, which is different from real id in IGDB API
"""

import datetime
import json
from urllib.parse import quote_plus

import httpx
import requests
from django.conf import settings
from django.core.cache import cache
from igdb.wrapper import IGDBWrapper
from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.search.models import ExternalSearchResultItem

_cache_key = "igdb_access_token"


def _igdb_access_token():
    if not settings.IGDB_CLIENT_SECRET:
        return "<missing>"
    try:
        token = cache.get(_cache_key)
        if not token:
            j = requests.post(
                f"https://id.twitch.tv/oauth2/token?client_id={settings.IGDB_CLIENT_ID}&client_secret={settings.IGDB_CLIENT_SECRET}&grant_type=client_credentials"
            ).json()
            token = j["access_token"]
            ttl = j["expires_in"] - 60
            cache.set(_cache_key, token, ttl)
    except Exception as e:
        logger.error("unable to obtain IGDB token", extra={"exception": e})
        token = "<invalid>"
    return token


def search_igdb_by_3p_url(steam_url):
    r = IGDB.api_query("websites", f'fields *, game.*; where url = "{steam_url}";')
    if not r:
        return None
    r = sorted(r, key=lambda w: w["game"]["id"])
    return IGDB(url=r[0]["game"]["url"])


@SiteManager.register
class IGDB(AbstractSite):
    SITE_NAME = SiteName.IGDB
    ID_TYPE = IdType.IGDB
    URL_PATTERNS = [
        r"\w+://www\.igdb\.com/games/([a-zA-Z0-9\-_]+)",
        r"\w+://m\.igdb\.com/games/([a-zA-Z0-9\-_]+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.igdb.com/games/" + id_value

    @classmethod
    def api_query(cls, p, q):
        key = "igdb:" + p + "/" + q
        if get_mock_mode():
            r = BasicDownloader(key).download().json()
        else:
            _wrapper = IGDBWrapper(settings.IGDB_CLIENT_ID, _igdb_access_token())
            try:
                r = json.loads(_wrapper.api_request(p, q))  # type: ignore
            except requests.HTTPError as e:
                logger.error(f"IGDB API: {e}", extra={"exception": e})
                return []
            if settings.DOWNLOADER_SAVEDIR:
                with open(
                    settings.DOWNLOADER_SAVEDIR + "/" + get_mock_file(key),
                    "w",
                    encoding="utf-8",
                ) as fp:
                    fp.write(json.dumps(r))
        return r

    def scrape(self):
        fields = "*, cover.url, genres.name, platforms.name, involved_companies.*, involved_companies.company.name"
        r = self.api_query("games", f'fields {fields}; where url = "{self.url}";')
        if not r:
            raise ParseError(self, "no data")
        r = r[0]
        brief = r["summary"] if "summary" in r else ""
        brief += "\n\n" + r["storyline"] if "storyline" in r else ""
        developer = None
        publisher = None
        release_date = None
        genre = None
        platform = None
        if "involved_companies" in r:
            developer = next(
                iter(
                    [
                        c["company"]["name"]
                        for c in r["involved_companies"]
                        if c["developer"]
                    ]
                ),
                None,
            )
            publisher = next(
                iter(
                    [
                        c["company"]["name"]
                        for c in r["involved_companies"]
                        if c["publisher"]
                    ]
                ),
                None,
            )
        if "platforms" in r:
            ps = sorted(r["platforms"], key=lambda p: p["id"])
            platform = [(p["name"] if p["id"] != 6 else "Windows") for p in ps]
        if "first_release_date" in r:
            release_date = datetime.datetime.fromtimestamp(
                r["first_release_date"], datetime.timezone.utc
            ).strftime("%Y-%m-%d")
        if "genres" in r:
            genre = [g["name"] for g in r["genres"]]
        websites = self.api_query(
            "websites", f'fields *; where game.url = "{self.url}";'
        )
        steam_url = None
        official_site = None
        for website in websites:
            if website["category"] == 1:
                official_site = website["url"]
            elif website["category"] == 13:
                steam_url = website["url"]
        pd = ResourceContent(
            metadata={
                "localized_title": [{"lang": "en", "text": r["name"]}],
                "localized_description": [{"lang": "en", "text": brief}],
                "title": r["name"],
                "other_title": [],
                "developer": [developer] if developer else [],
                "publisher": [publisher] if publisher else [],
                "release_date": release_date,
                "genre": genre,
                "platform": platform,
                "brief": brief,
                "official_site": official_site,
                "igdb_id": r["id"],
                "cover_image_url": (
                    "https:" + r["cover"]["url"].replace("t_thumb", "t_cover_big")
                    if r.get("cover")
                    else None
                ),
            }
        )
        if steam_url:
            pd.lookup_ids[IdType.Steam] = SiteManager.get_site_cls_by_id_type(
                IdType.Steam
            ).url_to_id(steam_url)
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str
    ) -> list[ExternalSearchResultItem]:
        if category != "game":
            return []
        SEARCH_PAGE_SIZE = 5 if category == "all" else 10
        limit = SEARCH_PAGE_SIZE
        offset = (page - 1) * limit
        q = f'fields *, cover.url, genres.name, platforms.name, involved_companies.*, involved_companies.company.name; search "{quote_plus(q)}"; limit {limit}; offset {offset};'
        _wrapper = IGDBWrapper(settings.IGDB_CLIENT_ID, _igdb_access_token())
        async with httpx.AsyncClient() as client:
            try:
                url = IGDBWrapper._build_url("games")
                params = _wrapper._compose_request(q)
                response = await client.post(url, **params)
                rs = json.loads(response.content)
            except requests.HTTPError as e:
                logger.error(f"IGDB API: {e}", extra={"exception": e})
                rs = []
        result = []
        for r in rs:
            subtitle = ""
            if "first_release_date" in r:
                subtitle = datetime.datetime.fromtimestamp(
                    r["first_release_date"], datetime.timezone.utc
                ).strftime("%Y-%m-%d ")
            if "platforms" in r:
                ps = sorted(r["platforms"], key=lambda p: p["id"])
                subtitle += ",".join(
                    [(p["name"] if p["id"] != 6 else "Windows") for p in ps]
                )
            brief = r["summary"] if "summary" in r else ""
            brief += "\n\n" + r["storyline"] if "storyline" in r else ""
            cover = (
                "https:" + r["cover"]["url"].replace("t_thumb", "t_cover_big")
                if r.get("cover")
                else ""
            )
            result.append(
                ExternalSearchResultItem(
                    ItemCategory.Game,
                    SiteName.IGDB,
                    r["url"],
                    r["name"],
                    subtitle,
                    brief,
                    cover,
                )
            )
        return result
