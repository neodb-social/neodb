"""
MangaUpdates (https://www.mangaupdates.com)

The public REST API (https://api.mangaupdates.com/v1) needs no key. Every
series becomes an Edition, the same way anilist.py treats manga.

Series URLs carry a base36 slug (``/series/7z3yqqk/naruto``), which is also
what Wikidata P11149 stores, so the slug is the id value. The API only takes
the numeric form, ``int(slug, 36)``. Legacy ``series.html?id=N`` numbers are a
different id space that the API no longer serves, so they are not matched.
"""

import re
import threading
from typing import Any

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.common.rate_limit import RedisRateLimiter
from catalog.models import Edition, IdType, ItemCategory, SiteName
from catalog.search import ExternalSearchResultItem, record_search_failure
from common.models.lang import detect_language, normalize_languages

_API_URL = "https://api.mangaupdates.com/v1"

# The API publishes no limit; the site itself answers 429 quickly, so pace
# conservatively.
_RATE = 1.0

_limiter: RedisRateLimiter | None = None
_limiter_lock = threading.Lock()


def mangaupdates_limiter() -> RedisRateLimiter:
    """Singleton limiter for api.mangaupdates.com calls."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = RedisRateLimiter(
                    key="ratelimit:api.mangaupdates.com", rate=_RATE
                )
    return _limiter


_HEADERS = {
    "User-Agent": settings.NEODB_USER_AGENT,
    "Accept": "application/json",
}

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_EMPHASIS_RE = re.compile(r"\*{1,2}([^*]+)\*{1,2}")

_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# MangaUpdates types that imply the original language. Novel, Doujinshi and
# Artbook can be from anywhere, so they carry no language.
_LANGUAGE_BY_TYPE = {"Manga": "ja", "Manhwa": "ko", "Manhua": "zh", "OEL": "en"}


def _description(text: str | None) -> str:
    """Flatten the markdown description.

    Paragraphs holding links are publisher and translation link blocks
    ("**Official Translations:** English: [Viz](...)"), not synopsis, so they
    are dropped whole rather than left as bare labels.
    """
    paragraphs = re.split(r"\n\s*\n", (text or "").replace("\r", ""))
    kept = [p for p in paragraphs if not _MD_LINK_RE.search(p)]
    out = _MD_EMPHASIS_RE.sub(r"\1", "\n\n".join(kept))
    return re.sub(r"[ \t]+\n", "\n", out).strip()


def _orig_title(series: dict[str, Any]) -> str | None:
    """The associated title written in the original script, if any.

    Only the type-implied script is trusted: a Japanese manga's alternative
    titles include Chinese translations too, and kanji alone cannot tell the
    two apart, so Manga requires kana.
    """
    titles = [a.get("title") for a in series.get("associated") or [] if a.get("title")]
    match series.get("type"):
        case "Manga":
            return next((t for t in titles if _KANA_RE.search(t)), None)
        case "Manhwa":
            return next((t for t in titles if _HANGUL_RE.search(t)), None)
        case "Manhua":
            return next(
                (t for t in titles if _CJK_RE.search(t) and not _KANA_RE.search(t)),
                None,
            )
    return None


def _authors(series: dict[str, Any]) -> list[str]:
    """Author then Artist credits, one name per person.

    The same person is listed once per role, often with different casing
    ("Kishimoto Masashi" as Author, "KISHIMOTO Masashi" as Artist), so dedupe
    on author_id and keep the first spelling.
    """
    entries = series.get("authors") or []
    ordered = [a for a in entries if a.get("type") == "Author"] + [
        a for a in entries if a.get("type") != "Author"
    ]
    names: list[str] = []
    seen: set[Any] = set()
    for a in ordered:
        key = a.get("author_id") or a.get("name")
        name = (a.get("name") or "").strip()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _year(series: dict[str, Any]) -> int | None:
    m = re.search(r"\d{4}", str(series.get("year") or ""))
    return int(m.group()) if m else None


def _title_lang(title: str) -> str:
    # The display title is romanized or English; langdetect reads romaji as
    # anything from Finnish to Swahili, so latin titles are tagged en, as the
    # AniList site does for romaji.
    return "en" if title.isascii() else detect_language(title)


@SiteManager.register
class MangaUpdates(AbstractSite):
    SITE_NAME = SiteName.MangaUpdates
    ID_TYPE = IdType.MangaUpdates
    URL_PATTERNS = [
        r"\w+://(?:www\.)?mangaupdates\.com/series/([0-9a-z]+)",
    ]
    WIKI_PROPERTY_ID = "P11149"
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.mangaupdates.com/series/{id_value}"

    @classmethod
    def api_url(cls, id_value: str) -> str:
        return f"{_API_URL}/series/{int(id_value, 36)}"

    def scrape(self) -> ResourceContent:
        if not self.id_value:
            raise ParseError(self, "id")
        mangaupdates_limiter().acquire(timeout=30.0)
        series = (
            RetryDownloader(self.api_url(self.id_value), headers=_HEADERS)
            .download()
            .json()
        )
        title = (series.get("title") or "").strip()
        if not title:
            raise ParseError(self, "title")
        brief = _description(series.get("description"))
        others = [
            t
            for t in dict.fromkeys(
                a.get("title") for a in series.get("associated") or []
            )
            if t and t != title
        ]
        cover_url = ((series.get("image") or {}).get("url") or {}).get("original")
        language = _LANGUAGE_BY_TYPE.get(series.get("type") or "")
        data: dict[str, Any] = {
            "preferred_model": "Edition",
            "localized_title": [{"lang": _title_lang(title), "text": title}],
            "other_title": others or None,
            "orig_title": _orig_title(series),
            "author": _authors(series),
            "publisher": [
                p["publisher_name"]
                for p in series.get("publishers") or []
                if p.get("type") == "Original" and p.get("publisher_name")
            ],
            "pub_year": _year(series),
            "language": normalize_languages([language]) if language else None,
            "genre": [g["genre"] for g in series.get("genres") or [] if g.get("genre")],
            "localized_description": (
                [{"lang": detect_language(brief), "text": brief}] if brief else []
            ),
            "brief": brief,
            "cover_image_url": cover_url,
        }
        raw_img = None
        ext = None
        if cover_url:
            raw_img, ext = BasicImageDownloader.download_image(cover_url, None)
        return ResourceContent(
            metadata={k: v for k, v in data.items() if v is not None},
            cover_image=raw_img,
            cover_image_extention=ext,
        )

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in {"all", "book"}:
            return []
        results: list[ExternalSearchResultItem] = []
        # Never block the interactive search dispatcher waiting for a slot;
        # same policy as anilist.search_task.
        if not await mangaupdates_limiter().try_acquire_async():
            record_search_failure(cls.SITE_NAME.value, "throttled")
            return results
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{_API_URL}/series/search",
                    json={"search": q, "page": page, "perpage": page_size},
                    headers=_HEADERS,
                    timeout=5,
                )
                response.raise_for_status()
                # perpage is not always honoured, so cut client-side too
                for hit in (response.json().get("results") or [])[:page_size]:
                    r = hit.get("record") or {}
                    url = r.get("url")
                    if not url or not cls.validate_url(url):
                        continue
                    subtitle = " · ".join(
                        str(x) for x in (r.get("type"), _year(r)) if x
                    )
                    results.append(
                        ExternalSearchResultItem(
                            category=ItemCategory.Book,
                            source_site=cls.SITE_NAME,
                            source_url=cls.id_to_url(cls.url_to_id(url)),
                            title=r.get("title") or hit.get("hit_title") or "",
                            subtitle=subtitle,
                            brief=_description(r.get("description")),
                            cover_url=((r.get("image") or {}).get("url") or {}).get(
                                "original"
                            )
                            or "",
                        )
                    )
            except httpx.TimeoutException:
                logger.warning("MangaUpdates search timeout", extra={"query": q})
                record_search_failure(cls.SITE_NAME.value, "timeout")
            except Exception as e:
                logger.error(
                    "MangaUpdates search error", extra={"query": q, "exception": e}
                )
                record_search_failure(cls.SITE_NAME.value, "error")
        return results
