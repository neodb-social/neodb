import re
from urllib.parse import quote_plus

import httpx
from loguru import logger

from catalog.book.utils import detect_isbn_asin, isbn_10_to_13
from catalog.common import *
from catalog.models import *
from common.models import detect_language


@SiteManager.register
class OpenLibrary(AbstractSite):
    SITE_NAME = SiteName.OpenLibrary
    ID_TYPE = IdType.OpenLibrary
    URL_PATTERNS = [
        r"https://openlibrary\.org/books/([^/\?]+M)",
        r"https://www\.openlibrary\.org/books/([^/\?]+M)",
    ]
    WIKI_PROPERTY_ID = "P648"
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://openlibrary.org/books/{id_value}"

    @classmethod
    def guess_id_type(cls, id_value):
        id_value = id_value.strip().upper()
        if re.match(r"^OL\d+M$", id_value):
            return IdType.OpenLibrary
        elif re.match(r"^OL\d+W$", id_value):
            return IdType.OpenLibrary_Work

    def scrape(self):
        # Use the Books API to get book information
        # id_value should always be an OpenLibrary book ID (OL...M format)
        api_url = f"https://openlibrary.org/api/books?bibkeys=OLID:{self.id_value}&jscmd=data&format=json"
        bibkey = f"OLID:{self.id_value}"

        response = BasicDownloader(api_url).download()
        data_json = response.json()

        if not data_json or bibkey not in data_json:
            raise ParseError(self, f"No data found for {bibkey}")

        book_data = data_json[bibkey]

        # Extract basic information
        title = book_data.get("title", "")
        subtitle = book_data.get("subtitle")

        # Authors
        authors = []
        if "authors" in book_data:
            authors = [
                author.get("name", "")
                for author in book_data["authors"]
                if author.get("name")
            ]

        # Publishers
        publishers = book_data.get("publishers", [])
        pub_house = publishers[0].get("name") if publishers else None

        # Publication date
        pub_year = None
        pub_month = None
        if "publish_date" in book_data:
            pub_date = book_data["publish_date"]
            # Try to parse different date formats
            date_match = re.search(r"(\d{4})", pub_date)
            if date_match:
                pub_year = int(date_match.group(1))
            # Try to extract month (basic pattern)
            month_match = re.search(r"(\d{1,2})[/-](\d{4})", pub_date)
            if month_match:
                pub_month = int(month_match.group(1))

        # Number of pages
        pages = book_data.get("number_of_pages")

        # Subjects (genres/topics)
        subjects = book_data.get("subjects", [])
        other_info = {}
        if subjects:
            other_info["subjects"] = [
                subj.get("name", "") for subj in subjects if subj.get("name")
            ]

        # Description
        brief = ""
        if "notes" in book_data and book_data["notes"]:
            brief = book_data["notes"]
        elif "description" in book_data:
            brief = book_data["description"]

        # Cover image
        img_url = None
        if "cover" in book_data:
            img_url = (
                book_data["cover"].get("large")
                or book_data["cover"].get("medium")
                or book_data["cover"].get("small")
            )

        # ISBNs
        isbn_10 = None
        isbn_13 = None
        lookup_ids = {}

        if "identifiers" in book_data:
            identifiers = book_data["identifiers"]
            if "isbn_10" in identifiers:
                isbn_10 = identifiers["isbn_10"][0] if identifiers["isbn_10"] else None
            if "isbn_13" in identifiers:
                isbn_13 = identifiers["isbn_13"][0] if identifiers["isbn_13"] else None

        # Use ISBN 13 as primary, convert from ISBN 10 if needed
        isbn = isbn_13
        if not isbn and isbn_10:
            isbn = isbn_10_to_13(isbn_10)

        if isbn:
            isbn_type, isbn_value = detect_isbn_asin(isbn)
            if isbn_type:
                lookup_ids[isbn_type] = isbn_value

        # Language detection
        lang = detect_language(title + " " + (brief or ""))
        language = []
        if "languages" in book_data:
            language = [
                lang_obj.get("key", "").replace("/languages/", "")
                for lang_obj in book_data["languages"]
            ]

        # Work information - OpenLibrary books are linked to works
        work_info = None
        if "works" in book_data and book_data["works"]:
            work = book_data["works"][0]
            work_key = work.get("key", "")
            if work_key.startswith("/works/"):
                work_id = work_key.replace("/works/", "")
                work_info = {
                    "model": "Work",
                    "id_type": IdType.OpenLibrary_Work,
                    "id_value": work_id,
                    "title": title,
                    "url": f"https://openlibrary.org{work_key}",
                }

        # Download cover image
        raw_img, ext = BasicImageDownloader.download_image(img_url, None, headers={})

        metadata = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "subtitle": subtitle,
            "localized_subtitle": [{"lang": lang, "text": subtitle}]
            if subtitle
            else [],
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
            "localized_description": [{"lang": lang, "text": brief}] if brief else [],
            "contents": None,
            "other_info": other_info,
            "cover_image_url": img_url,
        }

        if work_info:
            metadata["required_resources"] = [work_info]

        return ResourceContent(
            metadata=metadata,
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
        # OpenLibrary search API
        search_url = f"https://openlibrary.org/search.json?q={quote_plus(q)}&limit={page_size}&offset={(page - 1) * page_size}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(search_url, timeout=3)
                data = response.json()

                if "docs" in data:
                    for book in data["docs"]:
                        title = book.get("title", "Unknown Title")

                        # Build subtitle with author and publication year
                        subtitle_parts = []
                        if "author_name" in book:
                            subtitle_parts.append(
                                ", ".join(book["author_name"][:2])
                            )  # Limit to first 2 authors
                        if "first_publish_year" in book:
                            subtitle_parts.append(str(book["first_publish_year"]))
                        subtitle = " • ".join(subtitle_parts)

                        # Get first available edition
                        edition_key = None
                        if "edition_key" in book and book["edition_key"]:
                            edition_key = book["edition_key"][0]
                        elif "key" in book:
                            # This is a work key, we'll need to get editions
                            edition_key = None

                        # Prefer ISBN if available
                        url = None
                        if "isbn" in book and book["isbn"]:
                            url = f"https://openlibrary.org/isbn/{book['isbn'][0]}"
                        elif edition_key:
                            url = f"https://openlibrary.org/books/{edition_key}"
                        else:
                            continue  # Skip if we can't construct a URL

                        # Cover image
                        cover_url = ""
                        if "cover_i" in book:
                            cover_url = f"https://covers.openlibrary.org/b/id/{book['cover_i']}-M.jpg"

                        # Brief description
                        brief = ""
                        if "subtitle" in book:
                            brief = book["subtitle"]

                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Book,
                                SiteName.OpenLibrary,
                                url,
                                title,
                                subtitle,
                                brief,
                                cover_url,
                            )
                        )

            except httpx.ReadTimeout:
                logger.warning("OpenLibrary search timeout", extra={"query": q})
            except Exception as e:
                logger.error(
                    "OpenLibrary search error", extra={"query": q, "exception": e}
                )

        return results


@SiteManager.register
class OpenLibrary_Work(AbstractSite):
    SITE_NAME = SiteName.OpenLibrary
    ID_TYPE = IdType.OpenLibrary_Work
    WIKI_PROPERTY_ID = "P648"
    DEFAULT_MODEL = Work
    URL_PATTERNS = [
        r"https://openlibrary\.org/works/([^/\?]+W)",
        r"https://www\.openlibrary\.org/works/([^/\?]+W)",
    ]

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://openlibrary.org/works/{id_value}"

    def scrape(self):
        # Get work information from OpenLibrary API
        api_url = f"https://openlibrary.org/works/{self.id_value}.json"

        response = BasicDownloader(api_url).download()
        work_data = response.json()

        if not work_data:
            raise ParseError(self, f"No work data found for {self.id_value}")

        title = work_data.get("title", "")

        # Authors
        authors = []
        if "authors" in work_data:
            for author_ref in work_data["authors"]:
                # Author references in works are like {"author": {"key": "/authors/OL23919A"}}
                if "author" in author_ref and "key" in author_ref["author"]:
                    # We could fetch author details, but for now just skip
                    # For now, we'll skip fetching author names to keep it simple
                    pass

        # Description
        description = ""
        if "description" in work_data:
            if isinstance(work_data["description"], dict):
                description = work_data["description"].get("value", "")
            else:
                description = str(work_data["description"])

        # First published date
        first_published = None
        if "first_publish_date" in work_data:
            first_published = work_data["first_publish_date"]

        # Subjects
        subjects = work_data.get("subjects", [])

        # Language detection
        lang = detect_language(title + " " + description)

        # Find related editions (we could populate this, but it's complex)
        related_resources = []

        metadata = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "author": authors,
            "first_published": first_published,
            "localized_description": [{"lang": lang, "text": description}]
            if description
            else [],
            "subjects": subjects,
            "related_resources": related_resources,
        }

        return ResourceContent(metadata=metadata)
