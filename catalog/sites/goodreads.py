import json
from datetime import datetime
from urllib.parse import quote_plus

import httpx
from django.utils.timezone import make_aware
from loguru import logger
from lxml import html

from catalog.common import *
from catalog.models import (
    Edition,
    ExternalSearchResultItem,
    IdType,
    ItemCategory,
    SiteName,
    Work,
)
from catalog.models.utils import binding_to_format, detect_isbn_asin
from common.models import detect_language
from journal.models.renderers import html_to_text


class GoodreadsDownloader(RetryDownloader):
    def validate_response(self, response):
        if response is None:
            return RESPONSE_NETWORK_ERROR
        elif response.status_code == 200:
            if (
                response.text.find("__NEXT_DATA__") != -1
                and response.text.find('"title"') != -1
            ):
                return RESPONSE_OK
            # Goodreads may return legacy version for a/b testing
            # retry if so
            return RESPONSE_NETWORK_ERROR
        else:
            return RESPONSE_INVALID_CONTENT


@SiteManager.register
class Goodreads(AbstractSite):
    SITE_NAME = SiteName.Goodreads
    ID_TYPE = IdType.Goodreads
    WIKI_PROPERTY_ID = "P2968"
    DEFAULT_MODEL = Edition
    URL_PATTERNS = [
        r".+goodreads\.com/.*book/show/(\d+)",
        r".+goodreads\.com/.*book/(\d+)",
    ]

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.goodreads.com/book/show/" + id_value

    def scrape(self, response=None):
        data = {}
        if response is not None:
            h = html.fromstring(response.text.strip())
        else:
            dl = GoodreadsDownloader(self.url)
            h = dl.download().html()
        # Next.JS version of GoodReads
        # JSON.parse(document.getElementById('__NEXT_DATA__').innerHTML)['props']['pageProps']['apolloState']
        src = self.query_str(h, '//script[@id="__NEXT_DATA__"]/text()')
        if not src:
            raise ParseError(self, "__NEXT_DATA__ element")
        d = json.loads(src)["props"]["pageProps"]["apolloState"]
        o = {"Book": [], "Work": [], "Series": [], "Contributor": []}
        for v in d.values():
            t = v.get("__typename")
            if t and t in o:
                o[t].append(v)
        b = next(filter(lambda x: x.get("title"), o["Book"]), None)
        if not b:
            # Goodreads may return empty page template when internal service timeouts
            raise ParseError(self, "Book in __NEXT_DATA__ json")
        data["title"] = b["title"]
        data["brief"] = html_to_text(b["description"] or "").strip()
        lang = detect_language(b["title"] + " " + data["brief"])
        data["localized_title"] = [{"lang": lang, "text": b["title"]}]
        data["localized_subtitle"] = []  # Goodreads does not support subtitle
        if data["brief"]:
            data["brief"] = html_to_text(data["brief"])
        data["localized_description"] = (
            [{"lang": lang, "text": data["brief"]}] if data["brief"] else []
        )
        data["author"] = [c["name"] for c in o["Contributor"] if c.get("name")]
        ids = {}
        t, n = detect_isbn_asin(b["details"].get("asin"))
        if t:
            ids[t] = n
        # amazon has a known problem to use another book's isbn as asin
        # so we alway overwrite asin-converted isbn with real isbn
        t, n = detect_isbn_asin(b["details"].get("isbn13"))
        if t:
            ids[t] = n
        else:
            t, n = detect_isbn_asin(b["details"].get("isbn"))
            if t:
                ids[t] = n
        data["pages"] = b["details"].get("numPages")
        data["binding"] = b["details"].get("format")
        data["format"] = binding_to_format(b["details"].get("format"))
        data["pub_house"] = b["details"].get("publisher")
        if b["details"].get("publicationTime"):
            dt = make_aware(
                datetime.fromtimestamp(b["details"].get("publicationTime") / 1000)
            )
            data["pub_year"] = dt.year
            data["pub_month"] = dt.month
        if b["details"].get("language", {}).get("name"):
            data["language"] = [b["details"].get("language").get("name")]
        data["cover_image_url"] = b["imageUrl"]
        w = next(filter(lambda x: x.get("details"), o["Work"]), None)
        if w:
            data["required_resources"] = [
                {
                    "model": "Work",
                    "id_type": IdType.Goodreads_Work,
                    "id_value": str(w["legacyId"]),
                    "title": w["details"]["originalTitle"],
                    "url": w["editions"]["webUrl"],
                }
            ]
        pd = ResourceContent(metadata=data)
        pd.lookup_ids[IdType.ISBN] = ids.get(IdType.ISBN)
        pd.lookup_ids[IdType.ASIN] = ids.get(IdType.ASIN)
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["all", "book"]:
            return []
        p = (page - 1) * page_size // 20 + 1
        offset = (page - 1) * page_size % 20
        results = []
        search_url = f"https://www.goodreads.com/search?page={p}&q={quote_plus(q)}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(
                    search_url,
                    timeout=3,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": BasicDownloader.get_accept_language(),
                        "Accept-Encoding": "gzip, deflate",
                        "Connection": "keep-alive",
                        "DNT": "1",
                        "Upgrade-Insecure-Requests": "1",
                        "Cache-Control": "no-cache",
                    },
                )
                if r.url.path.startswith("/book/show/"):
                    # Goodreads will 302 if only one result matches ISBN
                    site = SiteManager.get_site_by_url(str(r.url))
                    if site:
                        res = site.get_resource_ready()
                        if res:
                            subtitle = f"{res.metadata.get('pub_year')} {', '.join(res.metadata.get('author', []))} {', '.join(res.metadata.get('translator', []))}"
                            results.append(
                                ExternalSearchResultItem(
                                    ItemCategory.Book,
                                    SiteName.Goodreads,
                                    res.url,
                                    res.metadata["title"],
                                    subtitle,
                                    res.metadata.get("brief", ""),
                                    res.metadata.get("cover_image_url", ""),
                                )
                            )
                else:
                    h = html.fromstring(r.content.decode("utf-8"))
                    books = h.xpath('//tr[@itemtype="http://schema.org/Book"]')
                    for c in books:  # type:ignore
                        el_cover = c.xpath('.//img[@class="bookCover"]/@src')
                        cover = el_cover[0] if el_cover else ""
                        el_title = c.xpath('.//a[@class="bookTitle"]//text()')
                        title = (
                            "".join(el_title).strip() if el_title else "Unkown Title"
                        )
                        el_url = c.xpath('.//a[@class="bookTitle"]/@href')
                        url = "https://www.goodreads.com" + el_url[0] if el_url else ""
                        el_authors = c.xpath('.//a[@class="authorName"]//text()')
                        subtitle = ", ".join(el_authors) if el_authors else ""
                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Book,
                                SiteName.Goodreads,
                                url,
                                title,
                                subtitle,
                                "",
                                cover,
                            )
                        )
            except httpx.ReadTimeout:
                logger.warning("Goodreads search timeout", extra={"query": q})
            except Exception as e:
                logger.error(
                    "Goodreads search error", extra={"query": q, "exception": e}
                )
        return results[offset : offset + page_size]


@SiteManager.register
class Goodreads_Work(AbstractSite):
    SITE_NAME = SiteName.Goodreads
    ID_TYPE = IdType.Goodreads_Work
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Work
    URL_PATTERNS = [r".+goodreads\.com/work/editions/(\d+)"]

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.goodreads.com/work/editions/" + id_value

    def scrape(self, response=None):
        content = BasicDownloader(self.url).download().html()
        title = self.query_str(content, "//h1/a/text()")
        if not title:
            raise ParseError(self, "title")
        author = self.query_str(content, "//h2/a/text()")
        try:
            first_published = self.query_str(content, "//h2/span/text()")
        except Exception:
            first_published = None
        pd = ResourceContent(
            metadata={
                "title": title,
                "localized_title": [{"lang": "en", "text": title}],
                "author": [author] if author else [],
                "first_published": first_published,
            }
        )
        return pd
