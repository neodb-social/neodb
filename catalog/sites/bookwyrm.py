import re
from urllib.parse import urlparse

from lxml.html import fromstring

from catalog.common import *
from catalog.models import Edition
from common.models import detect_language


@SiteManager.register
class Bookwyrm(AbstractSite):
    SITE_NAME = SiteName.Bookwyrm
    ID_TYPE = IdType.Bookwyrm
    DEFAULT_MODEL = Edition
    URL_PATTERNS = []

    @classmethod
    def id_to_url(cls, id_value):
        return id_value

    @classmethod
    def url_to_id(cls, url: str):
        return url

    @classmethod
    def validate_url_fallback(cls, url: str):
        parsed = urlparse(url)
        probe_url = "https://" + parsed.hostname + "/nodeinfo/2.0"  # type: ignore
        software = (
            CachedDownloader(probe_url).download().json().get("software").get("name")
        )
        if software == "bookwyrm":
            p = parsed.path
            if re.compile("^/book/[0-9]+").match(p):
                return True
            else:
                return False
        else:
            return False

    def scrape(self, response=None):
        r = BasicDownloader(self.id_value).download()
        tree = fromstring(r.text)
        data = {}
        title = "".join(tree.xpath("//h1[contains(@itemprop,'name')]//text()")).strip()  # type: ignore

        author = tree.xpath("//a[contains(@itemprop,'author')]//text()")
        isbn = "".join(tree.xpath("//dd[contains(@itemprop,'isbn')]//text()")).replace(  # type: ignore
            "-", ""
        )

        pub_date = (
            "".join(
                map(
                    str,
                    tree.xpath("//meta[contains(@itemprop,'datePublished')]/@content"),  # type: ignore
                )
            )
            .strip()
            .split("-")
        )

        pub_house = "".join(
            map(str, tree.xpath("//meta[contains(@itemprop,'publisher')]/@content"))  # type: ignore
        ).strip()

        cover_src = tree.xpath("//img[contains(@class,'book-cover')]/@src")[0]  # type: ignore
        pages = "".join(
            map(str, tree.xpath("//meta[contains(@itemprop,'numberOfPages')]/@content"))  # type: ignore
        ).strip()

        brief = "".join(
            tree.xpath("//div[contains(@itemprop,'abstract')]//text()")  # type: ignore
        ).strip()

        subtitle = "".join(
            map(
                str,
                tree.xpath(
                    "//meta[contains(@itemprop,'alternativeHeadline')]/@content"  # type: ignore
                ),
            )
        ).strip()

        series = "".join(
            tree.xpath(
                "//span[contains(@itemprop,'isPartOf')]//span[contains(@itemprop,'name')]//text()"  # type: ignore
            )
        ).strip()

        lang = detect_language(title + " " + brief) if brief else detect_language(title)

        book_base = "https://" + urlparse(self.id_value).hostname  # type: ignore
        if re.compile("^https://").match(cover_src):  # type: ignore
            data["cover_image_url"] = cover_src
        else:
            data["cover_image_url"] = book_base + cover_src if cover_src else None  # type: ignore

        if len(pub_date) == 3:
            data["pub_year"] = pub_date[0]
            data["pub_month"] = pub_date[1]

        data["pub_house"] = pub_house if pub_house else None

        data["pages"] = pages if pages else None

        data["isbn"] = isbn if isbn else None

        data["series"] = series if series else None

        data["author"] = author

        data["localized_title"] = [{"lang": lang, "text": title}]

        data["localized_subtitle"] = (
            [{"lang": lang, "text": subtitle}] if subtitle else None
        )

        data["localized_description"] = (
            [{"lang": lang, "text": brief}] if brief else None
        )

        pd = ResourceContent(
            metadata=data,
            lookup_ids={IdType.ISBN: isbn},
        )
        return pd
