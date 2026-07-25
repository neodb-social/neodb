"""
Readmoo 讀墨, a Taiwanese ebook store.

Book pages carry schema.org Book microdata, which is far more stable than
the surrounding presentation markup, so parsing keys off `itemprop` where
one is available and falls back to the labelled `<li>` rows otherwise.

Readmoo only sells ebooks. Pages may expose two identifiers: `isbn` is the
print edition's ISBN and `eisbn` the ebook's own. The print ISBN is the one
the item is looked up by, so items match those imported from the
print-oriented sites that populate most of the Chinese-language catalog
(BooksTW, Douban); the eISBN is kept alongside it as a backup rather than a
lookup id, since the ISBN slot in a resource's lookup ids is what becomes
the item's primary id. Titles published without a print counterpart are
identified by their eISBN.

Prices are taken from 定價, the list price, rather than 電子書售價, which
moves with promotions; Readmoo labels it 紙本書定價 when a paper edition
exists and 電子書定價 otherwise.
"""

import re

from lxml import etree

from catalog.common import *
from catalog.models import *
from catalog.models.utils import *
from common.models import normalize_price
from common.models.lang import normalize_language
from common.models.misc import uniq
from journal.models.renderers import html_to_text

# Readmoo appends volume counts to series names, e.g. "澈底對你成癮 （共 2 本）"
# and "莊子，從心開始【增訂紀念版】 （已完結）（共 4 本）"
_RE_SERIES_SUFFIX = re.compile(r"\s*(（共\s*\d+\s*本）|（已完結）)+\s*$")
# shown in place of the contributor list when a title has many authors
_MULTI_AUTHOR_PLACEHOLDER = "多位作者"
# the 詳細資訊 block runs on into contributor bios, which describe the people
# rather than the book and sometimes carry their personal contact details
_RE_BIO_HEADING = re.compile(r"^\s*(作者|譯者|繪者|編者)簡介\s*$", re.MULTILINE)


def _str(content, query: str) -> str:
    """First node matching `query` as text, with whitespace collapsed."""
    return " ".join(str(content.xpath(f"string({query})")).split())


def _meta_row(content, label: str) -> str:
    """Value of a `label：value` row in the publication metadata list."""
    for li in content.xpath("//ul[contains(@class,'book-meta-published')]/li"):
        text = " ".join(li.text_content().split())
        if text.startswith(label):
            # rows in this list mix fullwidth and ASCII colons
            parts = re.split(r"[：:]", text, maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
    return ""


def _clean_text(text: str) -> str:
    """Tidy text extracted from HTML: trim each line, cap blank runs."""
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@SiteManager.register
class Readmoo(AbstractSite):
    SITE_NAME = SiteName.Readmoo
    ID_TYPE = IdType.Readmoo
    URL_PATTERNS = [
        r"\w+://(?:www\.|m\.)?readmoo\.com/book/(\d+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://readmoo.com/book/" + id_value

    def scrape(self):
        assert self.url
        content = BasicDownloader(self.url).download().html()

        title = _str(content, "//h1[contains(@class,'book-detail-title')]")
        if not title:
            raise ParseError(self, "title")
        subtitle = _str(content, "//h2[contains(@class,'book-detail-subtitle')]")
        orig_title = _str(
            content, "//h2[contains(@class,'book-detail-original-title')]"
        )

        authors = []
        translators = []
        for li in content.xpath(
            "//ul[contains(@class,'book-meta-author')]"
            "/li[contains(@class,'contributors-list-item')]"
        ):
            label = "".join(li.xpath("text()")).strip()
            names = [
                " ".join(n.split())
                for n in li.xpath(
                    ".//span[@itemprop='author']//*[@itemprop='name']/text()"
                )
                if n.strip()
            ]
            # 原文作者 restates the author's name in the original language and
            # 插畫/繪者 has no matching Edition role, so neither is collected
            if label.startswith("作者"):
                authors += names
            elif label.startswith("譯者"):
                translators += names
        authors = [a for a in uniq(authors) if a != _MULTI_AUTHOR_PLACEHOLDER]
        translators = uniq(translators)

        publisher = _str(content, "//a[@itemprop='publisher']")

        pub_date = re.match(
            r"^(\d{4})[/-](\d{1,2})",
            _str(content, "//meta[@itemprop='datePublished']/@content"),
        )
        pub_year = int(pub_date[1]) if pub_date else None
        pub_month = int(pub_date[2]) if pub_date else None
        if pub_month and not 1 <= pub_month <= 12:
            pub_month = None

        language = normalize_language(_str(content, "//span[@itemprop='inLanguage']"))

        print_isbn = _str(content, "//span[@itemprop='isbn']")
        eisbn = _str(content, "//span[@itemprop='eisbn']")
        isbn = print_isbn or eisbn

        # "流動版面 EPUB" (reflowable) or "固定版面 EPUB"/"PDF" (fixed layout)
        binding = _meta_row(content, "商品格式")

        # only comics and magazines report a page count; 字數 (word count) is
        # reported for everything else and is not a page count
        pages = _meta_row(content, "頁數").replace(",", "")
        pages = int(pages) if pages.isdigit() and 0 < int(pages) < 1000000 else None

        # the price rows sit in a container that also matches on a looser
        # selector, which would run all of their labels together
        list_prices = {}
        for div in content.xpath(
            "//div[@id='main_page']"
            "//div[contains(concat(' ', normalize-space(@class), ' '), ' price ')]"
        ):
            label, _, value = " ".join(div.text_content().split()).partition("：")
            if value and label in ("紙本書定價", "電子書定價"):
                list_prices.setdefault(label, value.strip())
        price = (
            list_prices.get("紙本書定價")
            or list_prices.get("電子書定價")
            # neither list price is shown; 電子書售價 is all that is left
            or _str(content, "//div[@id='main_page']//*[@itemprop='price']")
        )
        currency = _str(
            content, "//div[@id='main_page']//meta[@itemprop='priceCurrency']/@content"
        )
        price = normalize_price(price, currency or "TWD") if price else None

        series = _RE_SERIES_SUFFIX.sub(
            "",
            _str(
                content,
                "//div[contains(@class,'installment')]"
                "/a[contains(@class,'text-link')][contains(@href,'/installment/')]",
            ),
        )

        brief = _clean_text(
            html_to_text(
                "".join(
                    etree.tostring(p, encoding="unicode", method="html")
                    for p in content.xpath("//div[@id='book-detail-description']/p")
                )
            )
        )
        bio = _RE_BIO_HEADING.search(brief)
        if bio:
            brief = brief[: bio.start()].strip()

        contents = "\n".join(
            " ".join(li.text_content().split())
            for li in content.xpath(
                "//div[@id='book-detail-contents']//li[contains(@class,'nav-point')]"
            )
        ).strip()

        img_url = _str(content, "//meta[@property='og:image']/@content") or None

        lang = language or "zh-tw"
        localized_subtitle = [{"lang": lang, "text": subtitle}] if subtitle else []
        localized_description = [{"lang": lang, "text": brief}] if brief else []
        data = {
            "title": title,
            "subtitle": subtitle or None,
            "localized_title": [{"lang": lang, "text": title}],
            "localized_subtitle": localized_subtitle,
            "localized_description": localized_description,
            "orig_title": orig_title or None,
            "author": authors,
            "translator": translators,
            "language": [language] if language else [],
            "publisher": [publisher] if publisher else [],
            "pub_year": pub_year,
            "pub_month": pub_month,
            "binding": binding or None,
            # Readmoo is an ebook store; every title is an ebook regardless of
            # whether the file is reflowable or fixed layout
            "format": Edition.BookFormat.EBOOK,
            "price": price,
            "pages": pages,
            "isbn": isbn or None,
            # recorded as a backup identifier when the print ISBN is the one
            # the item is looked up by
            "eisbn": eisbn if eisbn and eisbn != isbn else None,
            "brief": brief,
            "contents": contents or None,
            "series": series or None,
            "cover_image_url": img_url,
        }

        pd = ResourceContent(metadata=data)
        t, n = detect_isbn_asin(isbn)
        if t:
            pd.lookup_ids[t] = n
        pd.cover_image, pd.cover_image_extention = BasicImageDownloader.download_image(
            img_url, self.url
        )
        return pd
