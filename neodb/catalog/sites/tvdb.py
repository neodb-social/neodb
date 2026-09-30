"""
TheTVDB (https://thetvdb.com)

Uses the v4 API. A project key from https://thetvdb.com/api-information (the
TheTVDB API key site setting, seeded by TVDB_API_KEY) is exchanged at
POST /login for a bearer token that lasts a month; without a key the site is
inert. The free tier requires an attribution link on the site, see about.html.

Ids are TheTVDB's numeric ids, which is what Wikidata (P4835, P12397, P7043,
P12196, P7920) and TMDB external_ids store. Web pages use slugs instead
(/series/friends, /movies/the-matrix, /series/friends/seasons/official/1);
those resolve through the API in validate_url_fallback and are cached, so
url_to_id stays a cache read. /dereferrer/<type>/<id> urls, which redirect to
the slug pages, are the canonical urls.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from hashlib import md5
from typing import Any

import httpx
import pycountry
from django.conf import settings
from django.core.cache import cache

from catalog.common import *
from catalog.common.downloaders import DownloadError
from catalog.models import (
    IdType,
    ItemCategory,
    Movie,
    People,
    SiteName,
    TVEpisode,
    TVSeason,
    TVShow,
)
from catalog.search import ExternalSearchResultItem, record_search_failure
from catalog.sites.tmdb import query_tmdb_tvdb_id
from common.models import SiteConfig, normalize_country
from common.models.lang import SITE_PREFERRED_LANGUAGES, detect_language

_logger = logging.getLogger(__name__)

_API_URL = "https://api4.thetvdb.com/v4"
_WEB_URL = "https://thetvdb.com"
_HOST = r"^https?://(?:www\.)?thetvdb\.com"
# the token is valid for a month; renew well before it lapses
_TOKEN_TTL = 3600 * 24 * 25
_SLUG_TTL = 3600 * 24 * 30
_CAST_LIMIT = 10

# remoteIds[].type, from GET /sources/types: 2 IMDB title, 4 official site,
# 10/12/15 TMDB movie/tv/person, 16 IMDB person, 18 Wikidata
_SOURCE_OFFICIAL_SITE = 4
_SHOW_IDS = {2: IdType.IMDB, 12: IdType.TMDB_TV, 18: IdType.WikiData}
_MOVIE_IDS = {2: IdType.IMDB, 10: IdType.TMDB_Movie, 18: IdType.WikiData}
_EPISODE_IDS = {2: IdType.IMDB}
_PERSON_IDS = {16: IdType.IMDB, 15: IdType.TMDB_Person, 18: IdType.WikiData}

# metadata field -> characters[].peopleType (GET /people/types), in link order
_ROLES = {
    "director": {"Director"},
    "creator": {"Creator"},
    "playwright": {"Writer"},
    "producer": {"Producer", "Executive Producer"},
    "actor": {"Actor"},
}

# TheTVDB language codes are ISO 639-2/T plus a few of its own
_LANGUAGE_OVERRIDES = {
    "zhtw": "zh-tw",
    "yue": "zh-hk",
    # labelled "Português - Brasil" on TheTVDB, next to "por"
    "pt": "pt-br",
}


def _api_key() -> str:
    return SiteConfig.system.tvdb_api_key


def _headers(token: str | None = None) -> dict[str, str]:
    h = {"User-Agent": settings.NEODB_USER_AGENT, "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _download_error(url: str, msg: str) -> DownloadError:
    # A DownloadError, not a ParseError: TMDB and Wikidata imports schedule a
    # TheTVDB fetch through fetch_linked_resources on every instance, and only
    # DownloadError is treated there as an expected failure.
    downloader = BasicDownloader(url)
    downloader.response_type = RESPONSE_INVALID_CONTENT
    return DownloadError(downloader, msg)


def tvdb_token() -> str:
    """Bearer token, cached per key so a rotated key logs in again."""
    if get_mock_mode():
        return "mock"
    key = _api_key()
    login_url = f"{_API_URL}/login"
    if not key:
        raise _download_error(login_url, "TheTVDB API key is not configured")
    cache_key = "tvdb_token_" + md5(key.encode()).hexdigest()
    if token := cache.get(cache_key):
        return token
    try:
        r = httpx.post(
            login_url,
            json={"apikey": key},
            headers=_headers(),
            timeout=SiteConfig.system.downloader_request_timeout,
        )
        r.raise_for_status()
        token = r.json()["data"]["token"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise _download_error(login_url, f"TheTVDB login failed: {e}") from e
    cache.set(cache_key, token, _TOKEN_TTL)
    return token


def tvdb_get(path: str) -> dict[str, Any]:
    """GET an API path and return its `data`."""
    downloader = BasicDownloader(_API_URL + path, headers=_headers(tvdb_token()))
    data = downloader.download().json()
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise DownloadError(downloader, "unexpected TheTVDB response")
    return data["data"]


def _language(code: str | None, text: str = "") -> str | None:
    if not code:
        return None
    code = code.lower()
    if code in _LANGUAGE_OVERRIDES:
        return _LANGUAGE_OVERRIDES[code]
    if code == "zho":
        return detect_language(text, hint="zh") if text else "zh"
    lang = pycountry.languages.get(alpha_3=code)
    if lang is None:
        return None
    return getattr(lang, "alpha_2", None) or code


def _wanted(lang: str, orig_lang: str | None) -> bool:
    # base languages: an original "zh" must keep its zh-cn and zh-tw texts
    base = lang.split("-")[0]
    return base in SITE_PREFERRED_LANGUAGES or (
        orig_lang is not None and base == orig_lang.split("-")[0]
    )


def _localized(
    entries: list[dict] | None, field: str, orig_lang: str | None
) -> list[dict[str, str]]:
    """Pick the site's preferred languages (and the original one) from a
    translations list, primary entry first.

    Aliases are dropped: they hold spin-off names, abbreviations and romaji
    filed under the original language ("SNK", "Shingeki no Kyojin" as jpn).
    """
    out: list[dict[str, str]] = []
    ranked = sorted(
        (t for t in entries or [] if not t.get("isAlias")),
        key=lambda t: not t.get("isPrimary"),
    )
    for t in ranked:
        text = (t.get(field) or "").strip()
        lang = _language(t.get("language"), text)
        if not text or not lang or not _wanted(lang, orig_lang):
            continue
        entry = {"lang": lang, "text": text}
        if entry not in out:
            out.append(entry)
    return out


def _translations(
    record: dict, orig_lang: str | None
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    tr = record.get("translations") or {}
    titles = _localized(tr.get("nameTranslations"), "name", orig_lang)
    descs = _localized(tr.get("overviewTranslations"), "overview", orig_lang)
    # the top-level fields are not always in the original language (an
    # English name on a Japanese series), so detect rather than assume
    name = (record.get("name") or "").strip()
    if name and name not in [t["text"] for t in titles]:
        titles.append({"lang": detect_language(name, hint=orig_lang), "text": name})
    overview = (record.get("overview") or "").strip()
    if overview and overview not in [t["text"] for t in descs]:
        descs.append(
            {"lang": detect_language(overview, hint=orig_lang), "text": overview}
        )
    return titles, descs


def _orig_title(record: dict) -> str:
    orig = (record.get("originalLanguage") or "").lower()
    tr = (record.get("translations") or {}).get("nameTranslations") or []
    for t in tr:
        if (t.get("language") or "").lower() == orig and not t.get("isAlias"):
            if t.get("name"):
                return t["name"].strip()
    return (record.get("name") or "").strip()


def _brief(descs: list[dict[str, str]], orig_lang: str | None) -> str:
    for want in ("en", orig_lang):
        for d in descs:
            if d["lang"] == want:
                return d["text"]
    return descs[0]["text"] if descs else ""


def _remote_ids(record: dict, mapping: dict[int, IdType]) -> dict[IdType, str]:
    ids: dict[IdType, str] = {}
    for r in record.get("remoteIds") or []:
        id_type = mapping.get(r.get("type"))
        value = str(r.get("id") or "").strip()
        if id_type and value and id_type not in ids:
            ids[id_type] = value
    return ids


def _official_site(record: dict) -> str | None:
    for r in record.get("remoteIds") or []:
        value = str(r.get("id") or "")
        if r.get("type") == _SOURCE_OFFICIAL_SITE and value.startswith("http"):
            return value[:200]
    return None


def _release_date(value: str | None) -> str | None:
    return value if value and re.match(r"^\d{4}-\d{2}-\d{2}$", value) else None


def _person_url(person_id) -> str:
    return f"{_WEB_URL}/people/{person_id}"


def _credits(characters: list[dict] | None) -> dict[str, Any]:
    """Names per role plus the People resources to link, TMDB style.

    Links here and below carry no url: fetch_linked_resources would HEAD the
    dereferrer url and probe every fallback site with the slug page it lands
    on, while an id goes straight to the site class.
    """
    chars = sorted(characters or [], key=lambda c: c.get("sort") or 0)
    credits: dict[str, Any] = {}
    related: dict[str, dict] = {}
    for role, types in _ROLES.items():
        people: dict[Any, dict] = {}
        for c in chars:
            if c.get("peopleType") in types and c.get("personName"):
                people.setdefault(c.get("peopleId") or c["personName"], c)
        found = list(people.values())
        credits[role] = [c["personName"] for c in found]
        for c in found[:_CAST_LIMIT] if role == "actor" else found:
            if (pid := str(c.get("peopleId") or "")) and pid not in related:
                related[pid] = {
                    "model": "People",
                    "id_type": IdType.TVDB_Person,
                    "id_value": pid,
                    "title": c["personName"],
                }
    credits["related_people"] = list(related.values())
    return credits


def _base_metadata(
    d: dict, id_map: dict[int, IdType], creators_direct: bool = False
) -> tuple[dict[str, Any], dict[IdType, str], str | None]:
    """Metadata shared by series and movies, their lookup ids and the
    original language."""
    orig_lang = _language(d.get("originalLanguage"))
    titles, descs = _translations(d, orig_lang)
    credits = _credits(d.get("characters"))
    country = normalize_country(d.get("originalCountry") or "")
    lookup_ids = _remote_ids(d, id_map)
    metadata = {
        "localized_title": titles,
        "localized_description": descs,
        "title": d["name"],
        "orig_title": _orig_title(d),
        "imdb_code": lookup_ids.get(IdType.IMDB),
        # like TMDB, creators stand in when no series director is listed
        "director": credits["director"]
        or (credits["creator"] if creators_direct else []),
        "playwright": credits["playwright"],
        "actor": credits["actor"],
        "producer": credits["producer"],
        "genre": [g["name"] for g in d.get("genres") or [] if g.get("name")],
        "site": _official_site(d),
        "origin_country": [country] if country else [],
        "brief": _brief(descs, orig_lang),
        "cover_image_url": d.get("image") or None,
        "related_resources": credits["related_people"],
    }
    return metadata, lookup_ids, orig_lang


def _slug_cache_key(key: str) -> str:
    return "tvdb_slug_" + md5(key.encode()).hexdigest()


def _cached_id(key: str, resolve: Callable[[], str | None] | None = None) -> str | None:
    """The numeric id cached for a slug key, resolved and cached if absent."""
    cache_key = _slug_cache_key(key)
    tvdb_id = cache.get(cache_key)
    if not tvdb_id and resolve and (tvdb_id := resolve()):
        cache.set(cache_key, tvdb_id, _SLUG_TTL)
    return tvdb_id or None


def _slug_lookup(kind: str, slug: str) -> str | None:
    """Numeric id of a /series/ or /movies/ slug from the API."""
    if not _api_key() and not get_mock_mode():
        return None
    try:
        return str(tvdb_get(f"/{kind}/slug/{slug}").get("id") or "") or None
    except DownloadError as e:
        _logger.warning(f"TheTVDB slug lookup failed for {kind}/{slug}: {e}")
        return None


def _series_id(slug: str) -> str | None:
    return _cached_id(f"series:{slug}", lambda: _slug_lookup("series", slug))


class TVDB(AbstractSite):
    SITE_NAME = SiteName.TVDB
    # id-bearing url forms; slug forms go through validate_url_fallback
    URL_PATTERNS: list[str] = []
    SLUG_PATTERNS: list[str] = []
    DEREFERRER = ""
    # the API's /<kind>/slug/<slug> lookup for the one-group slug patterns
    SLUG_KIND = ""

    @classmethod
    def id_to_url(cls, id_value):
        return f"{_WEB_URL}/dereferrer/{cls.DEREFERRER}/{id_value}"

    @classmethod
    def _slug_key(cls, url: str) -> str | None:
        m = next((m for m in (re.match(p, url) for p in cls.SLUG_PATTERNS) if m), None)
        return f"{cls.DEREFERRER}:" + "/".join(m.groups()) if m else None

    @classmethod
    def _resolve_slug(cls, key: str) -> str | None:
        return _slug_lookup(cls.SLUG_KIND, key.split(":", 1)[1])

    @classmethod
    def url_to_id(cls, url: str):
        if tvdb_id := super().url_to_id(url):
            return tvdb_id
        key = cls._slug_key(url)
        return _cached_id(key) if key else None

    @classmethod
    def validate_url_fallback(cls, url: str) -> bool:
        key = cls._slug_key(url)
        return bool(key and _cached_id(key, lambda: cls._resolve_slug(key)))

    @classmethod
    def _search_result(
        cls, r: dict, category: ItemCategory
    ) -> ExternalSearchResultItem | None:
        tvdb_id = r.get("tvdb_id")
        if not tvdb_id or not r.get("name"):
            return None
        tr = r.get("translations") or {}
        title = tr.get(_site_language3()) or tr.get("eng") or r["name"]
        brief = (r.get("overviews") or {}).get(_site_language3()) or r.get(
            "overview", ""
        )
        subtitle = " ".join(
            str(x)
            for x in (r.get("year"), r["name"] if r["name"] != title else "")
            if x
        )
        return ExternalSearchResultItem(
            category,
            SiteName.TVDB,
            cls.id_to_url(tvdb_id),
            title,
            subtitle,
            brief,
            r.get("image_url") or r.get("thumbnail") or "",
        )


def _site_language3() -> str:
    match settings.LANGUAGE_CODE:
        case "zh-hans":
            return "zho"
        case "zh-hant":
            return "zhtw"
        case _:
            lang = pycountry.languages.get(alpha_2=settings.LANGUAGE_CODE[:2])
            return getattr(lang, "alpha_3", "eng") if lang else "eng"


@SiteManager.register
class TVDB_Series(TVDB):
    ID_TYPE = IdType.TVDB_Series
    URL_PATTERNS = [
        _HOST + r"/dereferrer/series/(\d+)",
        _HOST + r"/(?:index\.php)?\?tab=series&id=(\d+)",
    ]
    SLUG_PATTERNS = [_HOST + r"/series/([^/?#]+)/?(?:[?#].*)?$"]
    WIKI_PROPERTY_ID = "P4835"
    DEFAULT_MODEL = TVShow
    DEREFERRER = "series"
    SLUG_KIND = "series"

    @classmethod
    def extended(cls, series_id: str) -> dict[str, Any]:
        return tvdb_get(f"/series/{series_id}/extended?meta=translations")

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        d = self.extended(self.id_value)
        if not d.get("id") or not d.get("name"):
            raise ParseError(self, "name")
        metadata, lookup_ids, orig_lang = _base_metadata(
            d, _SHOW_IDS, creators_direct=True
        )
        default_type = d.get("defaultSeasonType") or 1
        seasons = sorted(
            (
                s
                for s in d.get("seasons") or []
                if (s.get("type") or {}).get("id") == default_type
            ),
            key=lambda s: s.get("number") or 0,
        )
        season_links = [
            {
                "model": "TVSeason",
                "id_type": IdType.TVDB_Season,
                "id_value": str(s["id"]),
                "title": f"Season {s.get('number')}",
            }
            for s in seasons
            if s.get("id")
        ]
        runtime = d.get("averageRuntime")
        metadata |= {
            "release_date": _release_date(d.get("firstAired")),
            "language": [orig_lang] if orig_lang else [],
            "season_count": len([s for s in seasons if s.get("number")]),
            "single_episode_length": runtime * 60 if runtime else None,
            "related_resources": season_links + metadata["related_resources"],
        }
        return ResourceContent(metadata=metadata, lookup_ids=lookup_ids)

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        # Silent when unconfigured: every search fans out here, and an
        # instance without a key must not log an error each time.
        if category not in ["movietv", "all", "movie", "tv"] or not _api_key():
            return []
        search_type = {"movie": "movie", "tv": "series"}.get(category)
        params: dict[str, Any] = {
            "query": q,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if search_type:
            params["type"] = search_type
        results: list[ExternalSearchResultItem] = []
        async with httpx.AsyncClient() as client:
            try:
                token = await asyncio.to_thread(tvdb_token)
                response = await client.get(
                    f"{_API_URL}/search",
                    params=params,
                    headers=_headers(token),
                    timeout=5,
                )
                response.raise_for_status()
                for r in response.json().get("data") or []:
                    match r.get("type"):
                        case "series":
                            item = TVDB_Series._search_result(r, ItemCategory.TV)
                        case "movie":
                            item = TVDB_Movie._search_result(r, ItemCategory.Movie)
                        case _:
                            item = None
                    if item:
                        results.append(item)
            except httpx.TimeoutException:
                _logger.warning("TheTVDB search timeout", extra={"query": q})
                record_search_failure(SiteName.TVDB.value, "timeout")
            except DownloadError as e:
                _logger.warning(
                    "TheTVDB search login failed", extra={"query": q, "exception": e}
                )
                record_search_failure(SiteName.TVDB.value, "error")
            except Exception as e:
                _logger.error(
                    "TheTVDB search error", extra={"query": q, "exception": e}
                )
                record_search_failure(SiteName.TVDB.value, "error")
        return results


@SiteManager.register
class TVDB_Season(TVDB):
    ID_TYPE = IdType.TVDB_Season
    URL_PATTERNS = [_HOST + r"/dereferrer/season/(\d+)"]
    SLUG_PATTERNS = [_HOST + r"/series/([^/?#]+)/seasons/([a-z]+)/(\d+)/?(?:[?#].*)?$"]
    WIKI_PROPERTY_ID = "P12397"
    DEFAULT_MODEL = TVSeason
    DEREFERRER = "season"

    @classmethod
    def _resolve_slug(cls, key: str) -> str | None:
        slug, season_type, number = key.split(":", 1)[1].split("/")
        series_id = _series_id(slug)
        if not series_id:
            return None
        try:
            d = TVDB_Series.extended(series_id)
        except DownloadError as e:
            _logger.warning(f"TheTVDB season lookup failed for {key}: {e}")
            return None
        return next(
            (
                str(s["id"])
                for s in d.get("seasons") or []
                if (s.get("type") or {}).get("type") == season_type
                and s.get("number") == int(number)
            ),
            None,
        )

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        d = tvdb_get(f"/seasons/{self.id_value}/extended")
        if not d.get("id") or not d.get("seriesId"):
            raise ParseError(self, "seriesId")
        series_id = str(d["seriesId"])
        show_site = TVDB_Series(id_value=series_id)
        show = show_site.get_resource_ready(auto_create=False, auto_link=False)
        if not show:
            raise ParseError(self, "show")
        number = d.get("number")
        title = f"Season {number}" if number else "Specials"
        episodes = sorted(
            (e for e in d.get("episodes") or [] if e.get("number") is not None),
            key=lambda e: e["number"],
        )
        aired = sorted(e["aired"] for e in episodes if _release_date(e.get("aired")))
        pd = ResourceContent(
            metadata={
                "title": title,
                "localized_title": [{"lang": "en", "text": title}],
                "season_number": number,
                "episode_number_list": [e["number"] for e in episodes],
                "episode_count": len(episodes),
                "release_date": aired[0] if aired else None,
                "origin_country": show.metadata.get("origin_country") or [],
                "language": show.metadata.get("language") or [],
                "cover_image_url": d.get("image") or None,
                "required_resources": [
                    {
                        "model": "TVShow",
                        "id_type": IdType.TVDB_Series,
                        "id_value": series_id,
                        "title": f"TheTVDB Series {series_id}",
                    }
                ],
            }
        )
        # Douban files a season under the show's IMDB id for season 1 and the
        # first episode's otherwise; match that, as TMDB_TVSeason does. The
        # id only helps matching, so a failed lookup leaves the season without.
        if number == 1:
            imdb = show.other_lookup_ids.get(IdType.IMDB)
            if imdb:
                pd.lookup_ids[IdType.IMDB] = imdb
        elif first_id := next((e["id"] for e in episodes if e.get("id")), None):
            try:
                ep = tvdb_get(f"/episodes/{first_id}/extended")
            except DownloadError as e:
                _logger.warning(f"TheTVDB episode lookup failed for {self}: {e}")
            else:
                imdb = _remote_ids(ep, _EPISODE_IDS).get(IdType.IMDB)
                if imdb:
                    pd.lookup_ids[IdType.IMDB] = imdb
        tmdb_season = self._tmdb_season(
            show.other_lookup_ids.get(IdType.TMDB_TV), number
        )
        if tmdb_season:
            pd.lookup_ids[IdType.TMDB_TVSeason] = tmdb_season
        return pd

    def _tmdb_season(self, tmdb_tv: str | None, number: int | None) -> str | None:
        """The TMDB season with this number, unless TMDB files it under
        another TheTVDB season: the two sites can number seasons apart, and a
        wrong match would merge two seasons into one item."""
        if not tmdb_tv or number is None:
            return None
        candidate = f"{tmdb_tv}-{number}"
        try:
            tvdb_id = query_tmdb_tvdb_id(IdType.TMDB_TVSeason, candidate)
        except Exception as e:
            # includes a 404: TMDB has no season with this number
            _logger.warning(f"TMDB season check failed for {candidate}: {e}")
            return None
        return candidate if tvdb_id in (None, self.id_value) else None


@SiteManager.register
class TVDB_Episode(TVDB):
    ID_TYPE = IdType.TVDB_Episode
    URL_PATTERNS = [
        _HOST + r"/series/[^/?#]+/episodes/(\d+)",
        _HOST + r"/dereferrer/episode/(\d+)",
        _HOST + r"/(?:index\.php)?\?tab=episode&seriesid=\d+&id=(\d+)",
    ]
    WIKI_PROPERTY_ID = "P7043"
    DEFAULT_MODEL = TVEpisode
    DEREFERRER = "episode"

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        d = tvdb_get(f"/episodes/{self.id_value}/extended?meta=translations")
        if not d.get("id"):
            raise ParseError(self, "id")
        season = next(
            (
                s
                for s in d.get("seasons") or []
                if (s.get("type") or {}).get("type") == "official"
            ),
            None,
        )
        season_number = d.get("seasonNumber")
        episode_number = d.get("number")
        titles, descs = _translations(d, None)
        title = next((t["text"] for t in titles if t["lang"] == "en"), None) or (
            d.get("name") or f"S{season_number} E{episode_number}"
        )
        pd = ResourceContent(
            metadata={
                "title": title,
                "brief": _brief(descs, None),
                "season_number": season_number,
                "episode_number": episode_number,
                "cover_image_url": d.get("image") or None,
            },
            lookup_ids=_remote_ids(d, _EPISODE_IDS),
        )
        if season and season.get("id"):
            pd.metadata["required_resources"] = [
                {
                    "model": "TVSeason",
                    "id_type": IdType.TVDB_Season,
                    "id_value": str(season["id"]),
                    "title": f"TheTVDB Season {season['id']}",
                }
            ]
        return pd


@SiteManager.register
class TVDB_Movie(TVDB):
    ID_TYPE = IdType.TVDB_Movie
    URL_PATTERNS = [_HOST + r"/dereferrer/movie/(\d+)"]
    SLUG_PATTERNS = [_HOST + r"/movies/([^/?#]+)/?(?:[?#].*)?$"]
    WIKI_PROPERTY_ID = "P12196"
    DEFAULT_MODEL = Movie
    DEREFERRER = "movie"
    SLUG_KIND = "movies"

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        d = tvdb_get(f"/movies/{self.id_value}/extended?meta=translations")
        if not d.get("id") or not d.get("name"):
            raise ParseError(self, "name")
        metadata, lookup_ids, orig_lang = _base_metadata(d, _MOVIE_IDS)
        languages = [
            lang
            for lang in (_language(c) for c in d.get("spoken_languages") or [])
            if lang
        ] or ([orig_lang] if orig_lang else [])
        runtime = d.get("runtime")
        metadata |= {
            "release_date": _release_date((d.get("first_release") or {}).get("date")),
            "language": list(dict.fromkeys(languages)),
            "length": runtime * 60 if runtime else None,
        }
        return ResourceContent(metadata=metadata, lookup_ids=lookup_ids)


@SiteManager.register
class TVDB_Person(TVDB):
    ID_TYPE = IdType.TVDB_Person
    URL_PATTERNS = [
        _HOST + r"/people/(\d+)",
        _HOST + r"/dereferrer/people/(\d+)",
    ]
    WIKI_PROPERTY_ID = "P7920"
    DEFAULT_MODEL = People
    SUPPORTS_PEOPLE_WORK_FETCH = True
    PEOPLE_WORKS_SOURCE_LABEL = "tvdb"

    @classmethod
    def id_to_url(cls, id_value):
        return _person_url(id_value)

    def _extended(self) -> dict[str, Any]:
        return tvdb_get(f"/people/{self.id_value}/extended?meta=translations")

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        d = self._extended()
        if not d.get("id") or not d.get("name"):
            raise ParseError(self, "name")
        tr = d.get("translations") or {}
        localized_name = _localized(tr.get("nameTranslations"), "name", None)
        name = d["name"].strip()
        if name not in [n["text"] for n in localized_name]:
            localized_name.insert(0, {"lang": detect_language(name), "text": name})
        # most biographies sit in the name translations, not in biographies
        localized_bio = _localized(d.get("biographies"), "biography", None)
        for bio in _localized(tr.get("nameTranslations"), "overview", None):
            if bio not in localized_bio:
                localized_bio.append(bio)
        return ResourceContent(
            metadata={
                "title": name,
                "localized_name": localized_name,
                "localized_bio": localized_bio,
                "birth_date": _release_date(d.get("birth")),
                "death_date": _release_date(d.get("death")),
                "cover_image_url": d.get("image") or None,
            },
            lookup_ids=_remote_ids(d, _PERSON_IDS),
        )

    def fetch_people_work_urls(self) -> list[str]:
        """Series and movie urls this person is credited on; [] on failure so
        background tasks stay resilient."""
        if not self.id_value:
            return []
        try:
            d = self._extended()
        except Exception as e:
            _logger.warning(
                f"TheTVDB people works fetch failed for {self.id_value}: {e}"
            )
            return []
        urls: set[str] = set()
        for c in d.get("characters") or []:
            if c.get("seriesId"):
                urls.add(TVDB_Series.id_to_url(c["seriesId"]))
            elif c.get("movieId"):
                urls.add(TVDB_Movie.id_to_url(c["movieId"]))
        return sorted(urls)
