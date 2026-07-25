"""
AniList (https://anilist.co)

A single GraphQL endpoint covers both anime and manga and needs no API key or
client registration. Anime maps to TVSeason/Movie, manga (including light
novels) maps to Edition, following the type dispatch bangumi.py already uses.

AniList also returns `idMal` on virtually every entry, which is stored as a
lookup id so a future MyAnimeList integration (and Wikidata's P4086/P4087)
lands on existing items instead of creating duplicates.
"""

import re
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

_API_URL = "https://graphql.anilist.co"

# AniList documents 90 requests/minute but the live x-ratelimit-limit header
# has been serving 30, so pace for the lower number.
_ANILIST_RATE = 0.5

_anilist_limiter: RedisRateLimiter | None = None
_anilist_limiter_lock = threading.Lock()


def anilist_limiter() -> RedisRateLimiter:
    """Singleton limiter for graphql.anilist.co calls."""
    global _anilist_limiter
    if _anilist_limiter is None:
        with _anilist_limiter_lock:
            if _anilist_limiter is None:
                _anilist_limiter = RedisRateLimiter(
                    key="ratelimit:graphql.anilist.co",
                    rate=_ANILIST_RATE,
                )
    return _anilist_limiter


_MEDIA_FIELDS = """
    id
    idMal
    type
    format
    status
    siteUrl
    description(asHtml: false)
    episodes
    duration
    chapters
    volumes
    countryOfOrigin
    startDate { year month day }
    genres
    synonyms
    title { romaji english native }
    coverImage { extraLarge large }
    studios { edges { isMain node { name } } }
    staff(sort: RELEVANCE, perPage: 25) { edges { role node { name { full } } } }
    externalLinks { site url }
"""

_MEDIA_QUERY = "query ($id: Int) { Media(id: $id) {" + _MEDIA_FIELDS + "} }"

_SEARCH_QUERY = """
query ($q: String, $type: MediaType, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(search: $q, type: $type) {
      id
      format
      title { romaji english native }
      startDate { year }
      coverImage { large }
      description(asHtml: false)
    }
  }
}
"""

# Anime formats that are standalone videos rather than a run of episodes.
# Everything else (TV, TV_SHORT, OVA, ONA, SPECIAL) becomes a TVSeason, which
# is also how bangumi.py treats OVA.
_MOVIE_FORMATS = {"MOVIE", "MUSIC"}

_DIRECTOR_ROLES = {"director", "chief director"}
_WRITER_ROLES = {"script", "screenplay", "series composition"}
# Matches "Story", "Art", "Story & Art", "Original Story"; not "Artist"/"Assistant".
_AUTHOR_ROLE_RE = re.compile(r"\b(story|art)\b")

# Roles carry a trailing scope, e.g. "Director (eps 1-479)" or
# "ADR Director (Italian; eps 287-348)". Strip it so the base role can be
# matched exactly, which keeps "Episode Director"/"Assistant Director" out of
# the director credit while still catching an episode-scoped main director.
_ROLE_SCOPE_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _base_role(role: str) -> str:
    return _ROLE_SCOPE_RE.sub("", role).strip().lower()

# The native title's language follows the work's country of origin.
_LANG_BY_COUNTRY = {"JP": "ja", "KR": "ko", "CN": "zh-cn", "TW": "zh-tw"}


def _parse_date(d: dict[str, Any] | None) -> str | None:
    """AniList FuzzyDate -> "YYYY-MM-DD", only when fully specified."""
    if not d or not d.get("year") or not d.get("month") or not d.get("day"):
        return None
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def _staff_names(media: dict[str, Any], match) -> list[str]:
    names = []
    for edge in (media.get("staff") or {}).get("edges") or []:
        role = _base_role(edge.get("role") or "")
        name = ((edge.get("node") or {}).get("name") or {}).get("full")
        if name and match(role) and name not in names:
            names.append(name)
    return names


def _main_studios(media: dict[str, Any]) -> list[str]:
    names = []
    for edge in (media.get("studios") or {}).get("edges") or []:
        if not edge.get("isMain"):
            continue
        name = (edge.get("node") or {}).get("name")
        if name and name not in names:
            names.append(name)
    return names


def _official_site(media: dict[str, Any]) -> str | None:
    for link in media.get("externalLinks") or []:
        if link.get("site") == "Official Site" and link.get("url"):
            return link["url"]
    return None


def _titles(media: dict[str, Any]) -> "OrderedDict[str, str | None]":
    """Ordered candidate titles mapped to a known language (None = detect)."""
    title = media.get("title") or {}
    country = media.get("countryOfOrigin")
    titles: OrderedDict[str, str | None] = OrderedDict()
    if title.get("romaji"):
        titles[title["romaji"]] = None
    if title.get("english"):
        titles.setdefault(title["english"], "en")
    if title.get("native"):
        titles.setdefault(title["native"], _LANG_BY_COUNTRY.get(country))
    for syn in media.get("synonyms") or []:
        if syn:
            titles.setdefault(syn, None)
    return titles


def _localized(titles: "OrderedDict[str, str | None]") -> list[dict[str, str]]:
    return [
        {"lang": lang or detect_language(text), "text": text}
        for text, lang in titles.items()
        if text
    ]


def _description(media: dict[str, Any]) -> str:
    # AniList descriptions carry <br>/<i> markup even with asHtml: false.
    return html_to_text(media.get("description") or "").strip()


class AniList(AbstractSite):
    """Shared fetch and mapping; subclasses bind a MediaType and URL path."""

    SITE_NAME = SiteName.AniList
    MEDIA_TYPE = ""
    URL_PATH = ""
    # bound by the subclasses
    MAL_ID_TYPE: IdType
    SEARCH_CATEGORIES: set[str] = set()

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://anilist.co/{cls.URL_PATH}/{id_value}"

    @classmethod
    def _post(cls, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        r = httpx.post(
            _API_URL,
            json=payload,
            headers={
                "User-Agent": settings.NEODB_USER_AGENT,
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def _fetch(self) -> dict[str, Any]:
        if not self.id_value:
            raise ParseError(self, "id")
        # One constant POST URL would collapse every mock fixture onto the same
        # filename, so key them by a synthetic string (as igdb.api_query does).
        key = f"anilist:media:{self.id_value}"
        if get_mock_mode():
            j = BasicDownloader(key).download().json()
        else:
            anilist_limiter().acquire(timeout=30.0)
            try:
                j = self._post(
                    {
                        "query": _MEDIA_QUERY,
                        "variables": {"id": int(self.id_value)},
                    },
                    SiteConfig.system.downloader_request_timeout,
                )
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(
                    "AniList fetch failed",
                    extra={"url": self.url, "exception": e},
                )
                raise ParseError(self, "media") from e
        media = (j.get("data") or {}).get("Media")
        if not media:
            raise ParseError(self, "media")
        return media

    def scrape(self) -> ResourceContent:
        media = self._fetch()
        titles = _titles(media)
        brief = _description(media)
        cover_url = (media.get("coverImage") or {}).get("extraLarge") or (
            media.get("coverImage") or {}
        ).get("large")
        data: dict[str, Any] = {
            "localized_description": (
                [{"lang": detect_language(brief), "text": brief}] if brief else []
            ),
            "brief": brief,
            "genre": media.get("genres") or [],
            "cover_image_url": cover_url,
        }
        data.update(self.parse_media(media, titles))

        raw_img = None
        ext = None
        if cover_url:
            raw_img, ext = BasicImageDownloader.download_image(cover_url, None)
        lookup_ids = {}
        if media.get("idMal"):
            lookup_ids[self.MAL_ID_TYPE] = str(media["idMal"])
        return ResourceContent(
            metadata={k: v for k, v in data.items() if v is not None},
            cover_image=raw_img,
            cover_image_extention=ext,
            lookup_ids=lookup_ids,
        )

    def parse_media(
        self, media: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def _search_category(cls, media: dict[str, Any]) -> ItemCategory:
        raise NotImplementedError

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in cls.SEARCH_CATEGORIES:
            return []
        results = []
        # Deliberately not rate-limited: search_task runs inside the interactive
        # external-search dispatcher (asyncio.gather over every searchable
        # site), and blocking a user's search to wait for a slot would freeze
        # the whole result page. Same reasoning as musicbrainz.search_task.
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    _API_URL,
                    json={
                        "query": _SEARCH_QUERY,
                        "variables": {
                            "q": q,
                            "type": cls.MEDIA_TYPE,
                            "page": page,
                            "perPage": page_size,
                        },
                    },
                    headers={
                        "User-Agent": settings.NEODB_USER_AGENT,
                        "Accept": "application/json",
                    },
                    timeout=5,
                )
                response.raise_for_status()
                j = response.json()
                for media in ((j.get("data") or {}).get("Page") or {}).get(
                    "media"
                ) or []:
                    titles = _titles(media)
                    if not titles:
                        continue
                    title = next(iter(titles))
                    year = (media.get("startDate") or {}).get("year")
                    subtitle = " · ".join(
                        str(x) for x in (media.get("format"), year) if x
                    )
                    results.append(
                        ExternalSearchResultItem(
                            category=cls._search_category(media),
                            source_site=cls.SITE_NAME,
                            source_url=cls.id_to_url(media["id"]),
                            title=title,
                            subtitle=subtitle,
                            brief=_description(media),
                            cover_url=(media.get("coverImage") or {}).get("large") or "",
                        )
                    )
            except httpx.TimeoutException:
                logger.warning("AniList search timeout", extra={"query": q})
                record_search_failure(cls.SITE_NAME.value, "timeout")
            except Exception as e:
                logger.error(
                    "AniList search error", extra={"query": q, "exception": e}
                )
                record_search_failure(cls.SITE_NAME.value, "error")
        return results


@SiteManager.register
class AniListAnime(AniList):
    ID_TYPE = IdType.AniList_Anime
    MAL_ID_TYPE = IdType.MAL_Anime
    MEDIA_TYPE = "ANIME"
    URL_PATH = "anime"
    URL_PATTERNS = [r"\w+://anilist\.co/anime/(\d+)"]
    WIKI_PROPERTY_ID = "P8729"
    MATCHABLE_MODELS = [TVSeason, Movie]
    SEARCH_CATEGORIES = {"all", "movietv", "movie", "tv"}

    @classmethod
    def _search_category(cls, media: dict[str, Any]) -> ItemCategory:
        return (
            ItemCategory.Movie
            if media.get("format") in _MOVIE_FORMATS
            else ItemCategory.TV
        )

    def parse_media(
        self, media: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        is_movie = media.get("format") in _MOVIE_FORMATS
        duration = media.get("duration")
        # single_episode_length and length are stored in seconds
        length = duration * 60 if duration else None
        title = media.get("title") or {}
        country = media.get("countryOfOrigin")
        data: dict[str, Any] = {
            "preferred_model": "Movie" if is_movie else "TVSeason",
            "localized_title": _localized(titles),
            "orig_title": title.get("native") or title.get("romaji"),
            "release_date": _parse_date(media.get("startDate")),
            "origin_country": [country] if country else [],
            "director": _staff_names(media, lambda r: r in _DIRECTOR_ROLES),
            "playwright": _staff_names(media, lambda r: r in _WRITER_ROLES),
            "producer": _main_studios(media),
            "site": _official_site(media),
        }
        if is_movie:
            data["length"] = length
        else:
            data["episode_count"] = media.get("episodes")
            data["single_episode_length"] = length
        return data


@SiteManager.register
class AniListManga(AniList):
    ID_TYPE = IdType.AniList_Manga
    MAL_ID_TYPE = IdType.MAL_Manga
    MEDIA_TYPE = "MANGA"
    URL_PATH = "manga"
    URL_PATTERNS = [r"\w+://anilist\.co/manga/(\d+)"]
    WIKI_PROPERTY_ID = "P8731"
    DEFAULT_MODEL = Edition
    SEARCH_CATEGORIES = {"all", "book"}

    @classmethod
    def _search_category(cls, media: dict[str, Any]) -> ItemCategory:
        return ItemCategory.Book

    def parse_media(
        self, media: dict[str, Any], titles: "OrderedDict[str, str | None]"
    ) -> dict[str, Any]:
        title = media.get("title") or {}
        start = media.get("startDate") or {}
        # Edition allows exactly one localized title (maxItems 1), so prefer a
        # latinized/English one for readability and keep the rest as other_title.
        primary = title.get("english") or title.get("romaji") or title.get("native")
        others = [t for t in titles if t != primary]
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
            "orig_title": title.get("native"),
            "author": _staff_names(media, lambda r: bool(_AUTHOR_ROLE_RE.search(r))),
            "pub_year": start.get("year"),
            "pub_month": start.get("month"),
        }
