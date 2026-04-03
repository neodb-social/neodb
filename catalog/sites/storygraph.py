import re

from catalog.common import *
from catalog.models import Edition, IdType, SiteName
from common.models import detect_language
from journal.models.renderers import html_to_text

_RE_PAGES = re.compile(r"(\d+)\s+pages?")
_RE_PUB_YEAR = re.compile(r"first pub(?:lished)?\s+(\d{4})", re.IGNORECASE)


class StoryGraphDownloader(ScrapDownloader):
    def __init__(self, url: str):
        super().__init__(url, wait_for_selector="div.book-title-author-and-series")

    def validate_response(self, response) -> int:
        if response is None:
            return RESPONSE_NETWORK_ERROR
        if response.status_code == 404:
            return RESPONSE_INVALID_CONTENT
        if response.status_code == 200:
            content = response.content.decode("utf-8", errors="replace")
            if "Just a moment" in content or "Enable JavaScript" in content:
                return RESPONSE_NETWORK_ERROR
            if "book-title-author-and-series" not in content:
                return RESPONSE_NETWORK_ERROR
            return RESPONSE_OK
        return RESPONSE_INVALID_CONTENT


@SiteManager.register
class StoryGraph(AbstractSite):
    SITE_NAME = SiteName.StoryGraph
    ID_TYPE = IdType.StoryGraph
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition
    URL_PATTERNS = [
        r"https?://app\.thestorygraph\.com/books/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    ]

    @classmethod
    def id_to_url(cls, id_value: str) -> str:
        return f"https://app.thestorygraph.com/books/{id_value}"

    def scrape(self) -> ResourceContent:
        assert self.url
        h = StoryGraphDownloader(self.url).download().html()

        # Title is in h3 inside div.book-title-author-and-series
        book_divs = h.xpath('.//div[contains(@class,"book-title-author-and-series")]')
        if not book_divs:
            raise ParseError(self, "book container")
        book_div = book_divs[0]

        title_els = book_div.xpath(".//h3")
        if not title_els:
            raise ParseError(self, "title element")
        title = title_els[0].text_content().strip()
        if not title:
            raise ParseError(self, "title text")

        # Authors are in p.font-body inside the book div; translators have a "(Role)" span
        authors: list[str] = []
        translators: list[str] = []
        author_p = book_div.xpath('.//p[contains(@class,"font-body")]')
        if author_p:
            for a in author_p[0].xpath('./a[starts-with(@href,"/authors")]'):
                name = a.text_content().strip()
                if not name:
                    continue
                role_spans = a.xpath("following-sibling::span[1]")
                role = role_spans[0].text_content().strip() if role_spans else ""
                if "(" in role:
                    translators.append(name)
                else:
                    authors.append(name)

        # Pages and first-published year from toggle-edition-info-link spans
        pages: int | None = None
        pub_year: int | None = None
        for span in h.xpath('.//span[contains(@class,"toggle-edition-info-link")]'):
            span_text = span.text_content().strip()
            if pages is None:
                m = _RE_PAGES.search(span_text)
                if m:
                    try:
                        pages = int(m.group(1))
                    except ValueError:
                        pass
            if pub_year is None:
                m = _RE_PUB_YEAR.search(span_text)
                if m:
                    try:
                        pub_year = int(m.group(1))
                    except ValueError:
                        pass

        # Description from the jQuery .html() script (full text) or visible truncated p
        brief = ""
        for script_text in h.xpath(".//script/text()"):
            if (
                "trix-content mt-3" not in script_text
                or "Description" not in script_text
            ):
                continue
            # Unescape JS escapes then apply regex
            unescaped = script_text.replace("\\/", "/")
            m = re.search(
                r'Description</h4><div class="trix-content mt-3"><div>(.*?)</div>',
                unescaped,
                re.DOTALL,
            )
            if m:
                desc_html = m.group(1).replace('\\"', '"').replace("\\n", "\n")
                brief = html_to_text(desc_html).strip()
                break
        if not brief:
            desc_p = h.xpath('.//p[contains(@class,"trix-content")]')
            if desc_p:
                brief = desc_p[0].text_content().strip()

        # Tags from book-page-tag-section (deduplicate, preserve order)
        tag_texts: list[str] = []
        seen: set[str] = set()
        for t in h.xpath(
            './/div[contains(@class,"book-page-tag-section")]//span/text()'
        ):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                tag_texts.append(t)

        # Cover from OG meta
        cover_url = ""
        og_img = h.xpath('.//meta[@property="og:image"]/@content')
        if og_img:
            cover_url = og_img[0].strip()

        lang = detect_language(title + " " + brief) if title else "en"
        raw_img, ext = (
            BasicImageDownloader.download_image(cover_url, self.url, headers={})
            if cover_url
            else (None, None)
        )

        data: dict = {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "author": authors,
            "pages": pages,
            "pub_year": pub_year,
            "brief": brief,
            "localized_description": [{"lang": lang, "text": brief}] if brief else [],
            "cover_image_url": cover_url,
        }
        if translators:
            data["translator"] = translators

        return ResourceContent(
            metadata=data,
            cover_image=raw_img,
            cover_image_extention=ext,
        )
