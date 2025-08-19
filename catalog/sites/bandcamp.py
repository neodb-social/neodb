import json
import logging
import re
import urllib.parse

import dateparser
import dns.resolver
import httpx
from loguru import logger
from lxml import html

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


@SiteManager.register
class Bandcamp(AbstractSite):
    SITE_NAME = SiteName.Bandcamp
    ID_TYPE = IdType.Bandcamp
    URL_PATTERNS = [r"https://([a-z0-9\-]+.bandcamp.com/album/[^?#/]+).*"]
    URL_PATTERN_FALLBACK = r"https://([a-z0-9\-\.]+/album/[^?#/]+).*"
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://{id_value}"

    @classmethod
    def validate_url_fallback(cls, url):
        if re.match(cls.URL_PATTERN_FALLBACK, url) is None:
            return False
        parsed_url = urllib.parse.urlparse(url)
        hostname = parsed_url.netloc
        resolver = dns.resolver.Resolver()
        resolver.timeout = 1
        resolver.lifetime = 1
        try:
            answers = resolver.query(hostname, "CNAME")
            for rdata in answers:
                if str(rdata.target) == "dom.bandcamp.com.":  # type:ignore
                    return True
        except Exception:
            pass
        try:
            answers = resolver.query(hostname, "A")
            for rdata in answers:
                if str(rdata.address) == "35.241.62.186":  # type:ignore
                    return True
        except Exception:
            pass
        return False

    def scrape(self):
        content = BasicDownloader2(self.url).download().html()
        try:
            title = self.query_str(content, "//h2[@class='trackTitle']/text()")
            artist = [
                self.query_str(content, "//div[@id='name-section']/h3/span/a/text()")
            ]
        except IndexError:
            raise ValueError("given url contains no valid info")

        genre = []  # TODO: parse tags
        track_list = ""
        try:
            release_str = re.sub(
                r"releas\w+ ",
                "",
                self.query_str(
                    content, "//div[@class='tralbumData tralbum-credits']/text()"
                ),
            )
            release_datetime = dateparser.parse(release_str) if release_str else None
            release_date = (
                release_datetime.strftime("%Y-%m-%d") if release_datetime else None
            )
        except Exception:
            release_date = None
        duration = None
        company = None
        brief_nodes = content.xpath("//div[@class='tralbumData tralbum-about']/text()")
        brief = "".join(brief_nodes) if brief_nodes else ""  # type:ignore
        cover_url = self.query_str(content, "//div[@id='tralbumArt']/a/@href")
        bandcamp_page_data = json.loads(
            self.query_str(content, "//meta[@name='bc-page-properties']/@content")
        )
        bandcamp_album_id = bandcamp_page_data["item_id"]
        localized_title = [{"lang": detect_language(title), "text": title}]
        localized_desc = (
            [{"lang": detect_language(brief), "text": brief}] if brief else []
        )
        data = {
            "localized_title": localized_title,
            "localized_description": localized_desc,
            "title": title,
            "artist": artist,
            "genre": genre,
            "track_list": track_list,
            "release_date": release_date,
            "duration": duration,
            "company": company,
            "brief": brief,
            "bandcamp_album_id": bandcamp_album_id,
            "cover_image_url": cover_url,
        }
        pd = ResourceContent(metadata=data)
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category != "music":
            return []
        p = (page - 1) * page_size // 18 + 1
        offset = (page - 1) * page_size % 18
        results = []
        search_url = f"https://bandcamp.com/search?from=results&item_type=a&page={p}&q={urllib.parse.quote_plus(q)}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(search_url, timeout=2)
                h = html.fromstring(r.content.decode("utf-8"))
                albums = h.xpath('//li[@class="searchresult data-search"]')
                for c in albums:  # type:ignore
                    el_cover = c.xpath('.//div[@class="art"]/img/@src')
                    cover = el_cover[0] if el_cover else ""
                    el_title = c.xpath('.//div[@class="heading"]//text()')
                    title = "".join(el_title).strip() if el_title else "Unknown Title"
                    el_url = c.xpath('..//div[@class="itemurl"]/a/@href')
                    url = el_url[0] if el_url else ""
                    el_authors = c.xpath('.//div[@class="subhead"]//text()')
                    subtitle = ", ".join(el_authors) if el_authors else ""
                    results.append(
                        ExternalSearchResultItem(
                            ItemCategory.Music,
                            SiteName.Bandcamp,
                            url,
                            title,
                            subtitle,
                            "",
                            cover,
                        )
                    )
            except httpx.ReadTimeout:
                logger.warning("Bandcamp search timeout", extra={"query": q})
            except Exception as e:
                logger.error(
                    "Bandcamp search error", extra={"query": q, "exception": e}
                )
        return results[offset : offset + page_size]
