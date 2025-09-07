import re
from urllib.parse import quote_plus

import httpx
from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.models.utils import detect_isbn_asin, isbn_10_to_13
from common.models import detect_language
from common.models.lang import normalize_language


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
        # id_value should always be an OpenLibrary book ID (OL...M format)
        api_url = f"https://openlibrary.org/books/{self.id_value}.json"
        response = BasicDownloader(api_url).download()
        book_data = response.json()
        if not book_data:
            raise ParseError(self, "no data returned")
        title = book_data.get("title", "")
        subtitle = book_data.get("subtitle")
        authors = []
        if "authors" in book_data:
            for a in book_data["authors"]:
                author_url = "https://openlibrary.org" + a["key"] + ".json"
                author_json = BasicDownloader(author_url).download().json()
                authors.append(author_json.get("name", ""))
        publishers = book_data.get("publishers", [])
        pub_house = publishers[0] if publishers else None
        pub_year = None
        pub_month = None
        if "publish_date" in book_data:
            pub_date = book_data["publish_date"]
            date_match = re.search(r"(\d{4})", pub_date)
            if date_match:
                pub_year = int(date_match.group(1))
            month_match = re.search(r"(\d{1,2})[/-](\d{4})", pub_date)
            if month_match:
                pub_month = int(month_match.group(1))
        pages = book_data.get("number_of_pages")
        other_info = {}
        brief = ""
        if "notes" in book_data and book_data["notes"]:
            brief = book_data["notes"]
        elif "description" in book_data:
            brief = book_data["description"]
        if isinstance(brief, dict):
            brief = brief.get("value", "")
        img_url = f"https://covers.openlibrary.org/b/olid/{self.id_value}-L.jpg"

        isbn_10 = book_data.get("isbn_10", [])
        isbn_13 = book_data.get("isbn_13", [])
        lookup_ids = {}
        isbn = isbn_13[0] if isbn_13 else None
        if not isbn and isbn_10:
            isbn = isbn_10_to_13(isbn_10[0])

        if isbn:
            isbn_type, isbn_value = detect_isbn_asin(isbn)
            if isbn_type:
                lookup_ids[isbn_type] = isbn_value

        language = []
        if "languages" in book_data:
            language = [
                lang_obj.get("key", "").replace("/languages/", "")
                for lang_obj in book_data["languages"]
            ]
        lang = (
            normalize_language(
                language[0]
                if len(language) > 0
                else detect_language(title + " " + (brief or ""))
            )
            or "en"
        )
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
        search_url = f"https://openlibrary.org/search.json?q={quote_plus(q)}&limit={page_size}&offset={(page - 1) * page_size}&fields=key,title,author_name,first_publish_year,editions,editions.key,editions.title,editions.language"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(search_url, timeout=3)
                data = response.json()
                if "docs" in data:
                    for work in data["docs"]:
                        title = work.get("title", "")
                        subtitle_parts = []
                        if "author_name" in work:
                            subtitle_parts.append(", ".join(work["author_name"][:2]))
                            if len(work["author_name"]) > 2:
                                subtitle_parts.append("et al.")
                        if "first_publish_year" in work:
                            subtitle_parts.append(str(work["first_publish_year"]))
                        subtitle = " • ".join(subtitle_parts)
                        editions = work.get("editions", {}).get("docs", [])
                        if not editions:
                            continue
                        k = editions[0]["key"].split("/")[-1]
                        title = editions[0].get("title", title)
                        url = f"https://openlibrary.org/books/{k}"
                        cover_url = f"https://covers.openlibrary.org/b/olid/{k}-M.jpg"
                        brief = ""
                        if "subtitle" in work:
                            brief = work["subtitle"]
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

    def fetch_editions(self, max_pages=5):
        """Fetch editions for this work from OpenLibrary editions API

        Args:
            max_pages: Maximum number of pages to fetch (default 5)

        Returns:
            List of edition resource dictionaries
        """
        editions = []
        offset = 0
        pages_fetched = 0

        while pages_fetched < max_pages:
            if offset == 0:
                api_url = f"https://openlibrary.org/works/{self.id_value}/editions.json"
            else:
                api_url = f"https://openlibrary.org/works/{self.id_value}/editions.json?offset={offset}"

            try:
                response = BasicDownloader(api_url).download()
                data = response.json()

                if "entries" not in data:
                    break

                for edition in data["entries"]:
                    edition_key = edition.get("key", "")
                    if edition_key.startswith("/books/"):
                        edition_id = edition_key.replace("/books/", "")
                        edition_title = edition.get("title", "")

                        # Create edition resource info
                        edition_resource = {
                            "model": "Edition",
                            "id_type": IdType.OpenLibrary,
                            "id_value": edition_id,
                            "title": edition_title,
                            "url": f"https://openlibrary.org{edition_key}",
                        }
                        editions.append(edition_resource)

                # Check for next page
                next_url = data.get("next")
                if not next_url:
                    break

                # Extract offset from next URL
                if "offset=" in next_url:
                    offset = int(next_url.split("offset=")[1].split("&")[0])
                else:
                    break

                pages_fetched += 1

            except Exception as e:
                logger.warning(f"Error fetching editions for {self.id_value}: {e}")
                break

        return editions

    def scrape(self):
        api_url = f"https://openlibrary.org/works/{self.id_value}.json"

        response = BasicDownloader(api_url).download()
        work_data = response.json()

        if not work_data:
            raise ParseError(self, f"No work data found for {self.id_value}")

        title = work_data.get("title", "")

        authors = []
        if "authors" in work_data:
            for author_ref in work_data["authors"]:
                author_url = (
                    "https://openlibrary.org" + author_ref["author"]["key"] + ".json"
                )
                author_json = BasicDownloader(author_url).download().json()
                authors.append(author_json.get("name", ""))

        description = ""
        if "description" in work_data:
            if isinstance(work_data["description"], dict):
                description = work_data["description"].get("value", "")
            else:
                description = str(work_data["description"])

        first_published = None
        if "first_publish_date" in work_data:
            first_published = work_data["first_publish_date"]

        lang = detect_language(title + " " + description)

        # Fetch related editions
        related_resources = self.fetch_editions()

        metadata = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "author": authors,
            "first_published": first_published,
            "localized_description": [{"lang": lang, "text": description}]
            if description
            else [],
            "related_resources": related_resources,
        }

        return ResourceContent(metadata=metadata)
