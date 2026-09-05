"""
MyAnimeList (https://myanimelist.net)

Uses the official API v2, which serves public data to any app registered at
https://myanimelist.net/apiconfig through the X-MAL-CLIENT-ID header. The id
comes from the MyAnimeList Client ID site setting (MAL_API_CLIENT_ID seeds
it); without one the site is inert. Anime maps to TVSeason/Movie and manga (light novels
included) to Edition, the same dispatch anilist.py uses. v2 exposes no staff
for anime, so anime carry their studios but no director credit.

AniList (idMal) and Wikidata (P4086/P4087) already file MAL ids as lookup
ids, so a fetch here lands on the item they created rather than a duplicate.
"""

import threading
from collections import OrderedDict
from typing import Any

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.common.rate_limit import RedisRateLimiter
from catalog.models import (
    Edition,
    IdType,
    ItemCategory,
    Movie,
    SiteName,
    TVSeason,
)
from catalog.search import ExternalSearchResultItem, record_search_failure
from common.models import SiteConfig
from common.models.lang import detect_language
from journal.models.renderers import html_to_text

# Shared title-language policy: romaji is assigned, never detected, and no two
# titles claim one language.
from .anilist import _UNKNOWN_LANG, _localized

_API_URL = "https://api.myanimelist.net/v2"

# MAL documents no limit for client-id access; stay well clear of it.
_RATE = 2.0

_limiter: RedisRateLimiter | None = None
_limiter_lock = threading.Lock()


def mal_limiter() -> RedisRateLimiter:
    """Singleton limiter for api.myanimelist.net calls."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = RedisRateLimiter(
                    key="ratelimit:api.myanimelist.net", rate=_RATE
                )
    return _limiter


def _client_id() -> str:
    return SiteConfig.system.mal_client_id


def _headers() -> dict[str, str]:
    return {
        "User-Agent": settings.NEODB_USER_AGENT,
        "Accept": "application/json",
        "X-MAL-CLIENT-ID": _client_id(),
    }


_COMMON_FIELDS = (
    "id,title,main_picture,alternative_titles,start_date,synopsis,media_type,"
    "status,genres"
)
_ANIME_FIELDS = _COMMON_FIELDS + ",num_episodes,average_episode_duration,studios"
_MANGA_FIELDS = (
    _COMMON_FIELDS + ",num_volumes,num_chapters,authors{first_name,last_name}"
)
_SEARCH_FIELDS = (
    "id,title,main_picture,alternative_titles,start_date,synopsis,media_type"
)

# Standalone videos; everything else (tv, ova, ona, special, tv_special, pv,
# cm) is a run of episodes and becomes a TVSeason.
_MOVIE_TYPES = {"movie", "music"}


def _parse_date(s: str | None) -> str | None:
    """MAL dates are "YYYY", "YYYY-MM" or "YYYY-MM-DD"; keep only full ones."""
    return s if s and len(s) == 10 else None


def _year_month(s: str | None) -> tuple[int | None, int | None]:
    parts = (s or "").split("-")
    year = int(parts[0]) if parts and parts[0].isdigit() else None
    month = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return year, month


def _titles(node: dict[str, Any]) -> "OrderedDict[str, str | None]":
    """Ordered candidate titles mapped to a known language (None = detect)."""
    alt = node.get("alternative_titles") or {}
    romaji, english, native = node.get("title"), alt.get("en"), alt.get("ja")
    titles: OrderedDict[str, str | None] = OrderedDict()
    if english:
        titles[english] = "en"
        if romaji:
            titles.setdefault(romaji, _UNKNOWN_LANG)
    elif romaji:
        titles[romaji] = "en"
    if native:
        # The field is labelled ja but holds the native title of Chinese and
        # Korean works too; kana settles it, and the hint breaks kanji ties.
        titles.setdefault(native, detect_language(native, "ja"))
    for syn in alt.get("synonyms") or []:
        if syn:
            titles.setdefault(syn, None)
    return titles


def _description(node: dict[str, Any]) -> str:
    return html_to_text(node.get("synopsis") or "").strip()


def _names(items: list[dict[str, Any]] | None, key: str) -> list[str]:
    names: list[str] = []
    for it in items or []:
        name = (it.get(key) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _author_names(node: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for edge in node.get("authors") or []:
        person = edge.get("node") or {}
        name = " ".join(
            p for p in (person.get("first_name"), person.get("last_name")) if p
        ).strip()
        if name and name not in names:
            names.append(name)
    return names


class MyAnimeList(AbstractSite):
    """Shared fetch and mapping; subclasses bind the API path and fields."""

    SITE_NAME = SiteName.MyAnimeList
    API_PATH = ""
    FIELDS = ""
    SEARCH_CATEGORIES: set[str] = set()

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://myanimelist.net/{cls.API_PATH}/{id_value}"

    @classmethod
    def api_url(cls, id_value: str) -> str:
        return f"{_API_URL}/{cls.API_PATH}/{id_value}?fields={cls.FIELDS}"

    def _fetch(self) -> dict[str, Any]:
        if not self.id_value:
            raise ParseError(self, "id")
        # Fail with the cause rather than the API's bare 400.
        if not _client_id() and not get_mock_mode():
            raise ParseError(self, "MyAnimeList Client ID is not configured")
        mal_limiter().acquire(timeout=30.0)
        node = (
            RetryDownloader(self.api_url(self.id_value), headers=_headers())
            .download()
            .json()
        )
        if not node.get("id") or not node.get("title"):
            raise ParseError(self, "title")
        return node

    def scrape(self) -> ResourceContent:
        node = self._fetch()
        titles = _titles(node)
        brief = _description(node)
        picture = node.get("main_picture") or {}
        cover_url = picture.get("large") or picture.get("medium")
        data: dict[str, Any] = {
            "localized_description": (
                [{"lang": detect_language(brief), "text": brief}] if brief else []
            ),
            "brief": brief,
            "genre": _names(node.get("genres"), "name"),
            "cover_image_url": cover_url,
        }
        data.update(self.parse_node(node, titles))
        raw_img = None
        ext = None
        if cover_url:
            raw_img, ext = BasicImageDownloader.download_image(cover_url, None)
        return ResourceContent(
            metadata={k: v for k, v in data.items() if v is not None},
            cover_image=raw_img,
            cover_image_extention=ext,
        )

    def parse_node(
        self, node: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def _search_category(cls, node: dict[str, Any]) -> ItemCategory:
        raise NotImplementedError

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        # Silent when unconfigured: every search fans out here, and an
        # instance without a client id must not log an error each time.
        if category not in cls.SEARCH_CATEGORIES or not _client_id():
            return []
        results: list[ExternalSearchResultItem] = []
        # Take a slot only when one is free; the interactive dispatcher must not
        # wait. burst=2 because the anime and manga sites both answer
        # category=all and so always reserve together (see anilist.search_task).
        if not await mal_limiter().try_acquire_async(burst=2):
            record_search_failure(cls.SITE_NAME.value, "throttled")
            return results
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{_API_URL}/{cls.API_PATH}",
                    params={
                        "q": q,
                        "limit": page_size,
                        "offset": (page - 1) * page_size,
                        "fields": _SEARCH_FIELDS,
                    },
                    headers=_headers(),
                    timeout=5,
                )
                response.raise_for_status()
                for entry in response.json().get("data") or []:
                    node = entry.get("node") or {}
                    titles = _titles(node)
                    if not titles or not node.get("id"):
                        continue
                    year, _ = _year_month(node.get("start_date"))
                    subtitle = " · ".join(
                        str(x) for x in (node.get("media_type"), year) if x
                    )
                    picture = node.get("main_picture") or {}
                    results.append(
                        ExternalSearchResultItem(
                            category=cls._search_category(node),
                            source_site=cls.SITE_NAME,
                            source_url=cls.id_to_url(node["id"]),
                            title=next(iter(titles)),
                            subtitle=subtitle,
                            brief=_description(node),
                            cover_url=picture.get("large")
                            or picture.get("medium")
                            or "",
                        )
                    )
            except httpx.TimeoutException:
                logger.warning("MyAnimeList search timeout", extra={"query": q})
                record_search_failure(cls.SITE_NAME.value, "timeout")
            except Exception as e:
                logger.error(
                    "MyAnimeList search error", extra={"query": q, "exception": e}
                )
                record_search_failure(cls.SITE_NAME.value, "error")
        return results


@SiteManager.register
class MyAnimeListAnime(MyAnimeList):
    ID_TYPE = IdType.MAL_Anime
    API_PATH = "anime"
    FIELDS = _ANIME_FIELDS
    URL_PATTERNS = [
        r"\w+://myanimelist\.net/anime/(\d+)",
        r"\w+://myanimelist\.net/anime\.php\?id=(\d+)",
    ]
    WIKI_PROPERTY_ID = "P4086"
    MATCHABLE_MODELS = [TVSeason, Movie]
    SEARCH_CATEGORIES = {"all", "movietv", "movie", "tv"}

    @classmethod
    def _search_category(cls, node: dict[str, Any]) -> ItemCategory:
        return (
            ItemCategory.Movie
            if node.get("media_type") in _MOVIE_TYPES
            else ItemCategory.TV
        )

    def parse_node(
        self, node: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        is_movie = node.get("media_type") in _MOVIE_TYPES
        # already in seconds; 0 means unknown
        length = node.get("average_episode_duration") or None
        alt = node.get("alternative_titles") or {}
        data: dict[str, Any] = {
            "preferred_model": "Movie" if is_movie else "TVSeason",
            "localized_title": _localized(titles),
            "orig_title": alt.get("ja") or node.get("title"),
            "release_date": _parse_date(node.get("start_date")),
            "producer": _names(node.get("studios"), "name"),
        }
        if is_movie:
            data["length"] = length
        else:
            data["episode_count"] = node.get("num_episodes") or None
            data["single_episode_length"] = length
        return data


@SiteManager.register
class MyAnimeListManga(MyAnimeList):
    ID_TYPE = IdType.MAL_Manga
    API_PATH = "manga"
    FIELDS = _MANGA_FIELDS
    URL_PATTERNS = [
        r"\w+://myanimelist\.net/manga/(\d+)",
        r"\w+://myanimelist\.net/manga\.php\?id=(\d+)",
    ]
    WIKI_PROPERTY_ID = "P4087"
    DEFAULT_MODEL = Edition
    SEARCH_CATEGORIES = {"all", "book"}

    @classmethod
    def _search_category(cls, node: dict[str, Any]) -> ItemCategory:
        return ItemCategory.Book

    def parse_node(
        self, node: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        alt = node.get("alternative_titles") or {}
        # Edition allows exactly one localized title, so prefer a readable
        # latin one and keep the rest as other_title.
        primary = alt.get("en") or node.get("title") or alt.get("ja")
        others = [t for t in titles if t != primary]
        year, month = _year_month(node.get("start_date"))
        return {
            "preferred_model": "Edition",
            "localized_title": (
                [
                    {
                        "lang": titles.get(primary) or detect_language(primary),
                        "text": primary,
                    }
                ]
                if primary
                else []
            ),
            "other_title": others or None,
            "orig_title": alt.get("ja"),
            "author": _author_names(node),
            "pub_year": year,
            "pub_month": month,
        }
