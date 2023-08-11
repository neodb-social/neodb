import logging

from catalog.book.models import *
from catalog.book.utils import *
from catalog.common import *

from .douban import *

_logger = logging.getLogger(__name__)


@SiteManager.register
class BooksTW(AbstractSite):
    SITE_NAME = SiteName.BooksTW
    ID_TYPE = IdType.BooksTW
    URL_PATTERNS = [
        r"\w+://www\.books\.com\.tw/products/(\w+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.books.com.tw/products/" + id_value

    def scrape(self):
        content = BasicDownloader(self.url).download().html()

        isbn_elem = content.xpath(
            "//div[@class='bd']/ul/li[starts-with(text(),'ISBN：')]/text()"
        )
        isbn = isbn_elem[0].strip().split("：", 1)[1].strip() if isbn_elem else None  # type: ignore

        # isbn_elem = content.xpath(
        #     "//div[@class='bd']/ul/li[starts-with(text(),'EISBN')]/text()"
        # )
        # eisbn = isbn_elem[0].strip().split("：", 1)[1].strip() if isbn_elem else None

        title = content.xpath("string(//h1)")
        if not title:
            raise ParseError(self, "title")
        subtitle = None
        orig_title = content.xpath("string(//h1/following-sibling::h2)")

        authors = content.xpath("string(//div/ul/li[contains(text(),'作者：')])")
        authors = authors.strip().split("：", 1)[1].split(",") if authors else []  # type: ignore
        if not authors:
            authors = [content.xpath("string(//div/ul/li[contains(.,'作者：')]/a)")]
        authors = [s.strip() for s in authors]  # type: ignore
        # author_orig = content.xpath("string(//div/ul/li[contains(text(),'原文作者：')])")

        translators = content.xpath("string(//div/ul/li[contains(text(),'譯者：')])")
        translators = (
            translators.strip().split("：", 1)[1].split(",") if translators else []  # type: ignore
        )
        translators = [s.strip() for s in translators]

        language_elem = content.xpath("//div/ul/li[starts-with(text(),'語言：')]/text()")
        language = (
            language_elem[0].strip().split("：")[1].strip() if language_elem else None  # type: ignore
        )

        pub_house = content.xpath("string(//div/ul/li[contains(text(),'出版社：')])")
        pub_house = (
            pub_house.strip().split("：", 1)[1].strip().split(" ", 1)[0]  # type: ignore
            if pub_house
            else None
        )

        pub_date = content.xpath("string(//div/ul/li[contains(text(),'出版日期：')])")
        pub_date = re.match(
            r"(\d+)/(\d+)/(\d+)\s*$",
            pub_date.strip().split("：", 1)[1].strip().split(" ", 1)[0]  # type: ignore
            if pub_date
            else "",
        )
        if pub_date:
            pub_year = int(pub_date[1])
            pub_month = int(pub_date[2])
        else:
            pub_year = None
            pub_month = None

        spec = content.xpath("string(//div/ul/li[contains(text(),'規格：')])")
        spec = spec.strip().split("：", 1)[1].strip().split("/") if spec else []  # type: ignore
        if len(spec) > 1:
            binding = spec[0].strip()
            pages = str(spec[1].strip()).split("頁")
            pages = int(pages[0]) if len(pages) > 1 else None
            if pages and (pages > 999999 or pages < 1):
                pages = None
        else:
            binding = None
            pages = None

        price = content.xpath("string(//div/ul/li[contains(text(),'定價：')])")
        price = (
            price.strip().split("：", 1)[1].split("元")[0].strip() + " NTD"  # type: ignore
            if price
            else None
        )

        series = content.xpath("string(//div/ul/li[contains(text(),'叢書系列：')]/a)")

        imprint = None

        brief = content.xpath("string(//h3[text()='內容簡介']/following-sibling::div)")

        contents = content.xpath("string(//h3[text()='目錄']/following-sibling::div)")

        img_url = content.xpath(
            "string(//div[contains(@class,'cover_img')]//img[contains(@class,'cover')]/@src)"
        )
        img_url = re.sub(r"&[wh]=\d+", "", img_url) if img_url else None  # type: ignore

        data = {
            "title": title,
            "subtitle": subtitle,
            "orig_title": orig_title,
            "author": authors,
            "translator": translators,
            "language": language,
            "pub_house": pub_house,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "binding": binding,
            "price": price,
            "pages": pages,
            "isbn": isbn,
            "brief": brief,
            "contents": contents,
            "series": series,
            "imprint": imprint,
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
