import re
from urllib.parse import quote_plus

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.models.utils import isbn_10_to_13


@SiteManager.register
class GoogleBooks(AbstractSite):
    SITE_NAME = SiteName.GoogleBooks
    ID_TYPE = IdType.GoogleBooks
    URL_PATTERNS = [
        r"https://books\.google\.[^/]+/books\?id=([^&#]+)",
        r"https://www\.google\.[^/]+/books/edition/[^/]+/([^&#?]+)",
        r"https://books\.google\.[^/]+/books/about/[^?]+\?id=([^&#?]+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://books.google.com/books?id=" + id_value

    def scrape(self):
        api_url = f"https://www.googleapis.com/books/v1/volumes/{self.id_value}"
        if settings.GOOGLE_API_KEY:
            api_url += f"?key={settings.GOOGLE_API_KEY}"
        b = BasicDownloader(api_url).download().json()
        other = {}
        title = b["volumeInfo"]["title"]
        subtitle = (
            b["volumeInfo"]["subtitle"] if "subtitle" in b["volumeInfo"] else None
        )
        pub_year = None
        pub_month = None
        if "publishedDate" in b["volumeInfo"]:
            pub_date = b["volumeInfo"]["publishedDate"].split("-")
            pub_year = pub_date[0]
            pub_month = pub_date[1] if len(pub_date) > 1 else None
        pub_house = (
            b["volumeInfo"]["publisher"] if "publisher" in b["volumeInfo"] else None
        )
        language = (
            b["volumeInfo"]["language"].lower() if "language" in b["volumeInfo"] else []
        )

        pages = b["volumeInfo"]["pageCount"] if "pageCount" in b["volumeInfo"] else None
        if "mainCategory" in b["volumeInfo"]:
            other["分类"] = b["volumeInfo"]["mainCategory"]
        authors = b["volumeInfo"]["authors"] if "authors" in b["volumeInfo"] else None
        if "description" in b["volumeInfo"]:
            brief = b["volumeInfo"]["description"]
        elif "textSnippet" in b["volumeInfo"]:
            brief = b["volumeInfo"]["textSnippet"]["searchInfo"]
        else:
            brief = ""
        brief = re.sub(r"<.*?>", "", brief.replace("<br", "\n<br"))
        img_url = None
        if "imageLinks" in b["volumeInfo"]:
            if "extraLarge" in b["volumeInfo"]["imageLinks"]:
                img_url = b["volumeInfo"]["imageLinks"]["extraLarge"]
            elif "large" in b["volumeInfo"]["imageLinks"]:
                img_url = b["volumeInfo"]["imageLinks"]["large"]
            elif "thumbnail" in b["volumeInfo"]["imageLinks"]:
                img_url = b["volumeInfo"]["imageLinks"]["thumbnail"]
            # if "thumbnail" in b["volumeInfo"]["imageLinks"]:
            #     img_url = b["volumeInfo"]["imageLinks"]["thumbnail"]
            #     img_url = img_url.replace("zoom=1", "")
        isbn10 = None
        isbn13 = None
        for iid in (
            b["volumeInfo"]["industryIdentifiers"]
            if "industryIdentifiers" in b["volumeInfo"]
            else []
        ):
            if iid["type"] == "ISBN_10":
                isbn10 = iid["identifier"]
            if iid["type"] == "ISBN_13":
                isbn13 = iid["identifier"]
        isbn = isbn13 if isbn13 is not None else isbn_10_to_13(isbn10)

        raw_img, ext = BasicImageDownloader.download_image(img_url, None, headers={})
        data = {
            "title": title,
            "localized_title": [{"lang": language, "text": title}],
            "subtitle": subtitle,
            "localized_subtitle": (
                [{"lang": language, "text": subtitle}] if subtitle else []
            ),
            "orig_title": None,
            "author": authors,
            "translator": None,
            "language": language,
            "pub_house": pub_house,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "binding": None,
            "pages": pages,
            "isbn": isbn,
            # "brief": brief,
            "localized_description": (
                [{"lang": language, "text": brief}] if brief else []
            ),
            "contents": None,
            "other_info": other,
            "cover_image_url": img_url,
        }
        return ResourceContent(
            metadata=data,
            cover_image=raw_img,
            cover_image_extention=ext,
            lookup_ids={IdType.ISBN: isbn13},
        )

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["all", "book"]:
            return []
        results = []
        api_url = f"https://www.googleapis.com/books/v1/volumes?country=us&q={quote_plus(q)}&startIndex={page_size * (page - 1)}&maxResults={page_size}&maxAllowedMaturityRating=MATURE"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(api_url, timeout=2)
                j = response.json()
                if "items" in j:
                    for b in j["items"]:
                        if "title" not in b["volumeInfo"]:
                            continue
                        title = b["volumeInfo"]["title"]
                        subtitle = ""
                        if "publishedDate" in b["volumeInfo"]:
                            subtitle += b["volumeInfo"]["publishedDate"] + " "
                        if "authors" in b["volumeInfo"]:
                            subtitle += ", ".join(b["volumeInfo"]["authors"])
                        if "description" in b["volumeInfo"]:
                            brief = b["volumeInfo"]["description"]
                        elif "textSnippet" in b["volumeInfo"]:
                            brief = b["volumeInfo"]["textSnippet"]["searchInfo"]
                        else:
                            brief = ""
                        category = ItemCategory.Book
                        # b['volumeInfo']['infoLink'].replace('http:', 'https:')
                        url = "https://books.google.com/books?id=" + b["id"]
                        cover = (
                            re.sub(
                                r"^http://",
                                "https://",
                                b["volumeInfo"]["imageLinks"]["thumbnail"],
                            )
                            if "imageLinks" in b["volumeInfo"]
                            else ""
                        )
                        results.append(
                            ExternalSearchResultItem(
                                category,
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
            except Exception as e:
                logger.error(
                    "GoogleBooks search error", extra={"query": q, "exception": e}
                )
        return results
