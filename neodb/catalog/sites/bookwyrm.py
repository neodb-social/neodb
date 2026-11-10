"""
BookWyrm editions, fetched as ActivityPub objects from the instance that hosts
them, e.g. https://bookwyrm.social/book/2183082

BookWyrm servers run on arbitrary hosts, so a URL is accepted only when its
host is a known BookWyrm server: a takahe Domain whose nodeinfo names
bookwyrm. Instances in secure mode refuse anonymous GETs; a GET signed as the
instance actor works there.
"""

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

from catalog.common import (
    AbstractSite,
    BasicImageDownloader,
    CachedDownloader,
    IdType,
    ResourceContent,
    SiteManager,
    SiteName,
)
from catalog.common.downloaders import RESPONSE_INVALID_CONTENT, DownloadError
from catalog.common.scrapers import ParseError
from catalog.models import Edition
from catalog.models.utils import binding_to_format, detect_isbn_asin
from common.models import detect_language
from common.models.lang import ISO_639_1, normalize_language
from common.validators import is_valid_url
from journal.models.renderers import html_to_text

logger = logging.getLogger(__name__)

_BOOK_PATH = re.compile(r"^/book/(\d+)/?$")
_OPENLIBRARY_EDITION = re.compile(r"^OL\d+M$")
_OPENLIBRARY_WORK = re.compile(r"^OL\d+W$")
_DIGITS = re.compile(r"^\d+$")
_PUBLISHED_DATE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?")


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


class BookWyrmMerged(ParseError):
    """The book was merged into another one on its own instance.

    Newer BookWyrm answers a merged-away book URL with the book that absorbed
    it, so the url asked for is never imported; canonical_url names the
    surviving book.
    """

    def __init__(self, site: AbstractSite, canonical_url: str):
        super().__init__(site, f"id (merged into {canonical_url})")
        self.canonical_url = canonical_url


@SiteManager.register
class BookWyrm(AbstractSite):
    SITE_NAME = SiteName.BookWyrm
    ID_TYPE = IdType.BookWyrm
    URL_PATTERNS = []
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition
    request_header = {
        "User-Agent": settings.NEODB_USER_AGENT,
        "Accept": "application/activity+json",
    }
    # BookWyrm's shipped nginx config caches every response for 1m, 403s
    # included, keyed on Accept but not on Signature. A signed retry with the
    # unsigned fetch's Accept would get that cached 403 back, so it asks for
    # another variant; BookWyrm serves any Accept containing "json" as AP.
    SIGNED_ACCEPT = (
        'application/ld+json; profile="https://www.w3.org/ns/activitystreams"'
    )
    FETCH_TIMEOUT = 5
    MAX_AUTHORS = 5

    @classmethod
    def book_host(cls, url: str) -> str | None:
        """The lowercase host of a BookWyrm edition URL, or None if url is not one."""
        try:
            u = urlparse(url)
            port = u.port
        except ValueError:
            return None
        if u.scheme not in ("https", "http") or port or not u.hostname:
            return None
        if not _BOOK_PATH.match(u.path):
            return None
        return u.hostname.lower()

    @classmethod
    def url_to_id(cls, url: str) -> str:
        host = cls.book_host(url)
        m = _BOOK_PATH.match(urlparse(url).path)
        if not host or not m:
            return url
        return f"https://{host}/book/{m[1]}"

    @classmethod
    def id_to_url(cls, id_value: str) -> str:
        return id_value

    @classmethod
    def host_refused(cls, host: str) -> bool:
        from takahe.utils import Takahe

        return (
            host in [d.lower() for d in settings.SITE_DOMAINS]
            or host in Takahe.get_blocked_peers()
        )

    @classmethod
    def is_bookwyrm_host(cls, host: str) -> bool:
        from takahe.utils import Takahe

        return host in Takahe.get_bookwyrm_peers()

    @classmethod
    def validate_url_fallback(cls, url: str) -> bool:
        # no fetch here: the peer list already says the host runs BookWyrm
        host = cls.book_host(url)
        return bool(host) and not cls.host_refused(host) and cls.is_bookwyrm_host(host)

    @classmethod
    def fetch_json(cls, url: str) -> Any:
        if not is_valid_url(url):
            raise ValueError(f"BookWyrm: refusing unsafe url {url}")
        try:
            resp = CachedDownloader(
                url, headers=cls.request_header, timeout=cls.FETCH_TIMEOUT
            ).download()
            try:
                return resp.json()
            except ValueError:
                # e.g. a bot challenge that redirects every request to HTML
                raise ValueError(
                    f"BookWyrm: no JSON at {url}, served from {resp.url}"
                ) from None
        except DownloadError as e:
            # a refusal (401/403 in secure mode) may pass when signed; a
            # timeout will not, so it is not retried
            if e.response_type != RESPONSE_INVALID_CONTENT:
                raise
            from takahe.auth import sign_get

            try:
                r = sign_get(url, timeout=cls.FETCH_TIMEOUT, accept=cls.SIGNED_ACCEPT)
                if r.status_code == 200:
                    return r.json()
            except (httpx.HTTPError, ValueError) as se:
                logger.warning(f"BookWyrm signed fetch failed: {url} {se}")
            raise e

    def fetch_book(self) -> dict[str, Any]:
        if not self.url:
            raise ParseError(self, "url")
        data = self.fetch_json(self.url)
        if not isinstance(data, dict):
            raise ParseError(self, "book")
        if data.get("type") not in ("Edition", "Work"):
            raise ParseError(self, f"type {data.get('type')}")
        data_id = data.get("id")
        if data_id != self.url:
            host = self.book_host(self.url)
            if isinstance(data_id, str) and host and self.book_host(data_id) == host:
                canonical = self.url_to_id(data_id)
                if canonical != self.url:
                    raise BookWyrmMerged(self, canonical)
            raise ParseError(self, f"id mismatch {data_id}")
        return data

    def fetch_authors(self, urls: Any) -> list[str]:
        host = self.book_host(self.url or "")
        names = []
        for url in urls[: self.MAX_AUTHORS] if isinstance(urls, list) else []:
            try:
                if not isinstance(url, str) or urlparse(url).hostname != host:
                    continue
                author = self.fetch_json(url)
                name = _str(author.get("name")) if isinstance(author, dict) else ""
                if name:
                    names.append(name)
            except DownloadError, ValueError:
                logger.warning(f"BookWyrm author fetch failed: {url}")
        return names

    @staticmethod
    def lookup_ids_from(data: dict[str, Any]) -> dict[IdType, str]:
        ids: dict[IdType, str] = {}
        for raw in (data.get("isbn13"), data.get("isbn10")):
            t, v = detect_isbn_asin(_str(raw))
            if t == IdType.ISBN and v:
                ids[IdType.ISBN] = v
                break
        ol = _str(data.get("openlibraryKey")).upper()
        if _OPENLIBRARY_EDITION.match(ol):
            ids[IdType.OpenLibrary] = ol
        elif data.get("type") == "Work" and _OPENLIBRARY_WORK.match(ol):
            ids[IdType.OpenLibrary_Work] = ol
        gr = _str(data.get("goodreadsKey"))
        if _DIGITS.match(gr):
            ids[IdType.Goodreads] = gr
        oclc = _str(data.get("oclcNumber"))
        if _DIGITS.match(oclc):
            ids[IdType.OCLC] = oclc
        return ids

    def scrape(self) -> ResourceContent:
        data = self.fetch_book()
        title = _str(data.get("title"))
        if not title:
            raise ParseError(self, "title")
        brief = html_to_text(_str(data.get("description"))).strip()
        languages = []
        for name in data.get("languages") or []:
            code = normalize_language(name)
            if code and code.split("-")[0] in ISO_639_1 and code not in languages:
                languages.append(code)
        lang = languages[0] if languages else detect_language(f"{title} {brief}")
        ids = self.lookup_ids_from(data)
        cover = data.get("cover")
        img_url = _str(cover.get("url") if isinstance(cover, dict) else cover)
        raw_img, img_ext = (
            BasicImageDownloader.download_image(img_url, None, headers={})
            if img_url
            else (None, None)
        )
        metadata: dict[str, Any] = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "author": self.fetch_authors(data.get("authors")),
            "language": languages,
            "localized_description": ([{"lang": lang, "text": brief}] if brief else []),
            "cover_image_url": img_url or None,
        }
        if data["type"] == "Work":
            metadata["preferred_model"] = "Work"
        else:
            metadata.update(self.edition_metadata(data, lang, ids))
        return ResourceContent(
            metadata=metadata,
            cover_image=raw_img,
            cover_image_extention=img_ext,
            lookup_ids=ids,
        )

    def parent_work_id(self, work: str) -> str | None:
        """The canonical url of this edition's Work, if it is one on this host.

        Only a Work may be the parent: it names no parent itself, so linking
        stops there, while an Edition named here could link back in a loop.
        """
        host = self.book_host(work)
        if not host or host != self.book_host(self.url or ""):
            return None
        work_id = self.url_to_id(work)
        if work_id == self.url:
            return None
        try:
            parent = self.fetch_json(work_id)
        except DownloadError, ValueError:
            return None
        if (
            isinstance(parent, dict)
            and parent.get("type") == "Work"
            and parent.get("id") == work_id
        ):
            return work_id
        return None

    def edition_metadata(
        self, data: dict[str, Any], lang: str, ids: dict[IdType, str]
    ) -> dict[str, Any]:
        subtitle = _str(data.get("subtitle"))
        pub_year = pub_month = None
        m = _PUBLISHED_DATE.match(_str(data.get("publishedDate")))
        if m:
            pub_year = int(m[1]) or None
            if m[2] and 1 <= int(m[2]) <= 12:
                pub_month = int(m[2])
        pages = data.get("pages")
        publishers = [_str(p) for p in data.get("publishers") or [] if _str(p)]
        physical_format = _str(data.get("physicalFormat"))
        series = _str(data.get("series"))
        d: dict[str, Any] = {
            "subtitle": subtitle or None,
            "localized_subtitle": (
                [{"lang": lang, "text": subtitle}] if subtitle else []
            ),
            "publisher": publishers[:1],
            "pub_year": pub_year,
            "pub_month": pub_month,
            "format": binding_to_format(physical_format),
            "binding": _str(data.get("physicalFormatDetail"))
            or physical_format
            or None,
            "pages": pages if isinstance(pages, int) and pages > 0 else None,
            "series": series or None,
            "isbn": ids.get(IdType.ISBN),
        }
        work_id = self.parent_work_id(_str(data.get("work")))
        if work_id:
            # by id, not url: the url route needs the host on the peer list
            d["required_resources"] = [
                {
                    "model": "Work",
                    "id_type": IdType.BookWyrm,
                    "id_value": work_id,
                    "title": _str(data.get("title")),
                }
            ]
        return d
