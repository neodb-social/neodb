import json
import re

from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.models.utils import detect_isbn_asin, isbn_10_to_13
from common.models import detect_language
from common.models.lang import normalize_language


@SiteManager.register
class WorldCat(AbstractSite):
    SITE_NAME = SiteName.WorldCat
    ID_TYPE = IdType.OCLC
    URL_PATTERNS = [
        r"https://search\.worldcat\.org/title/.+/oclc/(\d+)",
        r"https://search\.worldcat\.org/oclc/(\d+)",
        r"https://search\.worldcat\.org/title/(\d+)",
        r"https://search\.worldcat\.org/[a-zA-Z]+/title/(\d+)",
        r"https://www\.worldcat\.org/title/(\d+)",
        r"https://www\.worldcat\.org/oclc/(\d+)",
    ]
    WIKI_PROPERTY_ID = "P243"  # OCLC control number
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://search.worldcat.org/title/{id_value}"

    def scrape(self):
        response = BasicDownloader(self.url).download()
        content = response.html()

        # WorldCat embeds bibliographic data in JSON-LD format
        json_ld_scripts = content.xpath('//script[@type="application/ld+json"]/text()')

        if not json_ld_scripts:
            raise ParseError(self, "No JSON-LD data found")

        # Parse JSON-LD data
        book_data = None
        for script in json_ld_scripts:  # type:ignore
            try:
                data = json.loads(script)
                # Look for Book type in JSON-LD
                if isinstance(data, dict):
                    # Check if it's a DataFeed with dataFeedElement
                    if data.get("@type") == "DataFeed" and "dataFeedElement" in data:
                        elements = data["dataFeedElement"]
                        if isinstance(elements, list) and len(elements) > 0:
                            book_data = elements[0]
                            break
                    elif data.get("@type") in ["Book", "CreativeWork", "Product"]:
                        book_data = data
                        break
                    elif "@graph" in data:
                        # Sometimes JSON-LD uses @graph
                        for item in data["@graph"]:
                            if item.get("@type") in ["Book", "CreativeWork", "Product"]:
                                book_data = item
                                break
                        if book_data:
                            break
            except json.JSONDecodeError:
                continue

        if not book_data:
            raise ParseError(self, "No book data found in JSON-LD")

        # Extract title
        title = book_data.get("name", "")

        # Extract authors
        authors = []
        author_data = book_data.get("author", [])
        if not isinstance(author_data, list):
            author_data = [author_data]

        for author in author_data:
            if isinstance(author, dict):
                author_name = author.get("name", "")
                # name might be a list in WorldCat's structure
                if isinstance(author_name, list):
                    authors.extend(author_name)
                elif author_name:
                    authors.append(author_name)
            elif isinstance(author, str):
                authors.append(author)

        # WorldCat often has edition-specific details in workExample
        edition_data = None
        work_examples = book_data.get("workExample", [])
        if isinstance(work_examples, list) and len(work_examples) > 0:
            edition_data = work_examples[0]
        elif isinstance(work_examples, dict):
            edition_data = work_examples

        # Extract ISBN (prioritize edition data)
        isbn = None
        if edition_data:
            isbn = edition_data.get("isbn")
        if not isbn:
            isbn = book_data.get("isbn")

        lookup_ids = {}

        # OCLC number is the primary ID
        if self.id_value:
            lookup_ids[IdType.OCLC] = self.id_value

        # Handle ISBN
        if isbn:
            # ISBN can be a string or list
            if isinstance(isbn, list):
                isbn = isbn[0] if isbn else None

            if isbn:
                # Clean and detect ISBN type
                isbn = isbn.strip().replace("-", "").replace(" ", "")
                if len(isbn) == 10:
                    isbn = isbn_10_to_13(isbn) or ""

                isbn_type, isbn_value = detect_isbn_asin(isbn)
                if isbn_type:
                    lookup_ids[isbn_type] = isbn_value

        # Extract publication info (prioritize edition data)
        pub_year = None
        pub_month = None
        pub_house = None

        date_published = None
        if edition_data:
            date_published = edition_data.get("datePublished")
            # book_edition = edition_data.get("bookEdition")
        if not date_published:
            date_published = book_data.get("datePublished")

        if date_published:
            # Try to extract year
            year_match = re.search(r"(\d{4})", str(date_published))
            if year_match:
                pub_year = int(year_match.group(1))

        # Extract publisher (prioritize edition data)
        publisher_data = None
        if edition_data:
            publisher_data = edition_data.get("publisher")
        if not publisher_data:
            publisher_data = book_data.get("publisher")

        if publisher_data:
            if isinstance(publisher_data, dict):
                pub_house = publisher_data.get("name")
            elif isinstance(publisher_data, str):
                pub_house = publisher_data

        # Extract description
        description = book_data.get("description", "")

        # Extract number of pages
        pages = None
        num_pages = None
        if edition_data:
            num_pages = edition_data.get("numberOfPages")
        if not num_pages:
            num_pages = book_data.get("numberOfPages")

        if num_pages:
            try:
                pages = int(num_pages)
            except (ValueError, TypeError):
                pass

        # Detect language (prioritize edition data)
        in_language = None
        if edition_data:
            in_language = edition_data.get("inLanguage", "")
        if not in_language:
            in_language = book_data.get("inLanguage", "")

        if isinstance(in_language, dict):
            in_language = in_language.get("name", "")

        # Fallback to detection if not provided
        lang = (
            normalize_language(in_language)
            if in_language
            else detect_language(title + " " + description)
        )

        # Try to get cover image from Open Graph or other meta tags
        cover_image_url = None
        og_image = content.xpath('//meta[@property="og:image"]/@content')
        if og_image:
            cover_image_url = og_image[0]  # type:ignore

        # Download cover image if available
        raw_img = None
        ext = None
        if cover_image_url:
            try:
                raw_img, ext = BasicImageDownloader.download_image(
                    cover_image_url, self.url, headers={}
                )
            except Exception as e:
                logger.warning(f"Failed to download cover image: {e}")

        # Build metadata
        metadata = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}] if title else [],
            "author": authors,
            "language": [lang] if lang else [],
            "pub_house": pub_house,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "pages": pages,
            "isbn": isbn,
            "localized_description": [{"lang": lang, "text": description}]
            if description
            else [],
            "cover_image_url": cover_image_url,
        }

        return ResourceContent(
            metadata=metadata,
            cover_image=raw_img,
            cover_image_extention=ext,
            lookup_ids=lookup_ids,
        )
