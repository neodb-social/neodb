import logging
import re
from html import unescape
from urllib.parse import quote_plus

import httpx

from catalog.common import *
from catalog.models import *
from catalog.models.utils import isbn_10_to_13
from catalog.search import *
from common.models import SiteConfig, detect_language

logger = logging.getLogger(__name__)


@SiteManager.register
class GoogleBooks(AbstractSite):
    SITE_NAME = SiteName.GoogleBooks
    ID_TYPE = IdType.GoogleBooks
    _GOOGLE_HOST = (
        r"https?://(?:books|www)\.google\."
        r"(?:com|cat|[a-z]{2}|(?:com|co)\.[a-z]{2})"
    )
    # Capture only the volume ID, wherever it appears in the query. Reject
    # duplicate IDs and keep fragments out of query parameter matching.
    _ID_QUERY = (
        r"\?(?:(?!id=)[^&#]*&)*id=([A-Za-z0-9_-]+)"
        r"(?:&(?!id=)[^&#]*)*(?:#.*)?$"
    )
    URL_PATTERNS = [  # noqa: RUF012 - matches AbstractSite's URL_PATTERNS contract
        _GOOGLE_HOST + r"/books/?" + _ID_QUERY,
        _GOOGLE_HOST + r"/books/about/[^/?#]+" + _ID_QUERY,
        _GOOGLE_HOST + r"/books/edition/[^/?#]+/([A-Za-z0-9_-]+)/?(?:[?#].*)?$",
        r"https?://play\.google\.com/"
        r"(?:store/books/details(?:/[^/?#]+)?/?|books/reader/?)" + _ID_QUERY,
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @staticmethod
    def _description(volume):
        brief = (
            volume.get("volumeInfo", {}).get("description")
            or volume.get("searchInfo", {}).get("textSnippet")
            or ""
        )
        brief = re.sub(r"<br\s*/?>|</p>", "\n", brief, flags=re.IGNORECASE)
        return unescape(re.sub(r"<[^>]*>", "", brief)).strip()

    @staticmethod
    def _cover_url(volume, thumbnail=False):
        links = volume.get("volumeInfo", {}).get("imageLinks") or {}
        sizes = (
            "extraLarge",
            "large",
            "medium",
            "small",
            "thumbnail",
            "smallThumbnail",
        )
        if thumbnail:
            sizes = (
                "thumbnail",
                "smallThumbnail",
                "small",
                "medium",
                "large",
                "extraLarge",
            )
        url = next((links[size] for size in sizes if links.get(size)), None)
        return re.sub(r"^http://", "https://", url) if url else None

    @classmethod
    def id_to_url(cls, id_value):
        return "https://books.google.com/books?id=" + id_value

    def scrape(self):
        api_url = f"https://www.googleapis.com/books/v1/volumes/{self.id_value}"
        if SiteConfig.system.google_api_key:
            api_url += f"?key={quote_plus(SiteConfig.system.google_api_key)}"
        b = BasicDownloader(api_url).download().json()
        other = {}
        title = b["volumeInfo"]["title"]
        subtitle = b["volumeInfo"].get("subtitle")
        pub_year = None
        pub_month = None
        if "publishedDate" in b["volumeInfo"]:
            pub_date = b["volumeInfo"]["publishedDate"].split("-")
            pub_year = pub_date[0]
            pub_month = pub_date[1] if len(pub_date) > 1 else None
        pub_house = b["volumeInfo"].get("publisher")
        language = (
            b["volumeInfo"]["language"].lower() if "language" in b["volumeInfo"] else []
        )

        pages = b["volumeInfo"].get(
            "pageCount", b["volumeInfo"].get("printedPageCount")
        )
        categories = b["volumeInfo"].get("categories") or []
        if categories:
            other["分类"] = "; ".join(categories)
        elif b["volumeInfo"].get("mainCategory"):
            other["分类"] = b["volumeInfo"]["mainCategory"]
        authors = b["volumeInfo"].get("authors")
        brief = self._description(b)
        img_url = self._cover_url(b)
        isbn10 = None
        isbn13 = None
        oclc = None
        for iid in b["volumeInfo"].get("industryIdentifiers", []):
            if iid["type"] == "ISBN_10":
                isbn10 = iid["identifier"]
            if iid["type"] == "ISBN_13":
                isbn13 = iid["identifier"]
            if iid["type"] == "OTHER":
                match = re.fullmatch(r"OCLC:([0-9]+)", iid["identifier"])
                if match:
                    oclc = match[1]
        isbn = isbn13 if isbn13 is not None else isbn_10_to_13(isbn10)
        lookup_ids = {IdType.ISBN: isbn13}
        if oclc:
            lookup_ids[IdType.OCLC] = oclc

        raw_img, ext = BasicImageDownloader.download_image(img_url, None, headers={})
        # `language` is "" when the volume omits a language tag; the localized
        # label `lang` must be a non-empty string, so fall back to detection.
        label_lang = (
            language
            if isinstance(language, str) and language
            else detect_language(title)
        )
        data = {
            "title": title,
            "localized_title": [{"lang": label_lang, "text": title}],
            "subtitle": subtitle,
            "localized_subtitle": (
                [{"lang": label_lang, "text": subtitle}] if subtitle else []
            ),
            "orig_title": None,
            "author": authors,
            "translator": None,
            "language": language,
            "publisher": [pub_house] if pub_house else [],
            "pub_year": pub_year,
            "pub_month": pub_month,
            "binding": None,
            "pages": pages,
            "isbn": isbn,
            # "brief": brief,
            "localized_description": (
                [{"lang": label_lang, "text": brief}] if brief else []
            ),
            "contents": None,
            "other_info": other,
            "cover_image_url": img_url,
        }
        if b.get("saleInfo", {}).get("isEbook") is True:
            data["format"] = Edition.BookFormat.EBOOK
        return ResourceContent(
            metadata=data,
            cover_image=raw_img,
            cover_image_extention=ext,
            lookup_ids=lookup_ids,
        )

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["all", "book"]:
            return []
        results = []
        api_url = f"https://www.googleapis.com/books/v1/volumes?country=us&q={quote_plus(q)}&startIndex={page_size * (page - 1)}&maxResults={page_size}&maxAllowedMaturityRating=MATURE"
        if SiteConfig.system.google_api_key:
            api_url += f"&key={quote_plus(SiteConfig.system.google_api_key)}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(api_url, timeout=2)
                response.raise_for_status()
                j = response.json()
                if "items" in j:
                    for b in j["items"]:
                        if not b.get("volumeInfo", {}).get("title"):
                            continue
                        title = b["volumeInfo"]["title"]
                        subtitle = ""
                        if "publishedDate" in b["volumeInfo"]:
                            subtitle += b["volumeInfo"]["publishedDate"] + " "
                        if "authors" in b["volumeInfo"]:
                            subtitle += ", ".join(b["volumeInfo"]["authors"])
                        brief = cls._description(b)
                        # b['volumeInfo']['infoLink'].replace('http:', 'https:')
                        url = "https://books.google.com/books?id=" + b["id"]
                        cover = cls._cover_url(b, thumbnail=True) or ""
                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Book,
                                SiteName.GoogleBooks,
                                url,
                                title,
                                subtitle,
                                brief,
                                cover,
                            )
                        )
            except httpx.ReadTimeout:
                logger.warning("GoogleBooks search timeout", extra={"query": q})
                record_search_failure(SiteName.GoogleBooks.value, "timeout")
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ) as e:
                logger.error(
                    "GoogleBooks search error",
                    extra={"query": q, "exception": type(e).__name__},
                )
                record_search_failure(SiteName.GoogleBooks.value, "error")
        return results
