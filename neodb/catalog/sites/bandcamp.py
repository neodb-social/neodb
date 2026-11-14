import json
import logging
import re
import urllib.parse
from typing import cast

import dateparser
import dns.resolver
import httpx
from lxml import html
from lxml.html import HtmlElement

from catalog.common import *
from catalog.common.downloaders import DownloadError, ResponseType
from catalog.models import *
from catalog.models.utils import upc_to_gtin_13
from catalog.search import *
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)

_ALBUM_MARKERS = ("data-tralbum=", 'id="name-section"')
_CHALLENGE_MARKER = "Client Challenge"


class BandcampDownloader(ScrapDownloader, BasicDownloader2):
    """
    Bandcamp's CDN answers some clients with a JavaScript challenge page (HTTP
    200) in place of the album. The plain request goes first, because it is free
    and passes from most hosts; the scrapers are tried only when it is rejected.
    """

    def __init__(self, url: str):
        super().__init__(url, wait_for_selector="#name-section")
        self.challenged = False

    def validate_response(self, response) -> int:
        if response is None:
            return RESPONSE_NETWORK_ERROR
        if response.status_code in (404, 410):
            return RESPONSE_INVALID_CONTENT
        if response.status_code != 200:
            return RESPONSE_NETWORK_ERROR
        text = response.content.decode("utf-8", errors="replace")
        if any(marker in text for marker in _ALBUM_MARKERS):
            return RESPONSE_OK
        if _CHALLENGE_MARKER in text:
            self.challenged = True
        # retryable: another route may get the real page
        return RESPONSE_NETWORK_ERROR

    def get_scrapers(self) -> list[str]:
        providers = self.get_providers()
        backup = self.get_backup_provider()
        if backup and backup not in providers:
            # self-hosted and free, so ahead of the metered providers here
            providers.insert(0, backup)
        return providers

    def download(self) -> ResponseType:
        if get_mock_mode():
            return super().download()
        self._record_download_invocation()
        resp, resp_type = self._download(self.url)
        if resp_type != RESPONSE_OK and resp_type != RESPONSE_INVALID_CONTENT:
            for provider in self.get_scrapers():
                resp, resp_type = self._scrape_with_provider(provider)
                if resp_type == RESPONSE_OK and resp is not None:
                    resp_type = self.validate_response(resp)
                if resp_type == RESPONSE_OK:
                    break
        self.response_type = resp_type
        if resp_type == RESPONSE_OK and resp is not None:
            return resp
        raise DownloadError(
            self, "blocked by a bot challenge" if self.challenged else None
        )


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
    def url_to_id(cls, url: str):
        # Custom domains match only URL_PATTERN_FALLBACK; without this fallback
        # url_to_id returns None and scrape() asserts on self.url.
        id_value = super().url_to_id(url)
        if id_value:
            return id_value
        m = re.match(cls.URL_PATTERN_FALLBACK, url)
        return m[1] if m else None

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
                if str(rdata.target) == "dom.bandcamp.com.":
                    return True
        except Exception:
            pass
        try:
            answers = resolver.query(hostname, "A")
            for rdata in answers:
                if str(rdata.address) == "35.241.62.186":
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def _embedded_json(cls, content: HtmlElement, query: str) -> dict:
        try:
            data = json.loads(cls.query_str(content, query))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse_date(value: str | None) -> str | None:
        try:
            dt = dateparser.parse(value) if value else None
        except Exception:
            return None
        return dt.strftime("%Y-%m-%d") if dt else None

    @staticmethod
    def _parse_tracks(tralbum: dict, schema: dict) -> tuple[str, int | None]:
        tracks = []
        seconds = 0.0
        trackinfo = tralbum.get("trackinfo")
        for n, t in enumerate(trackinfo if isinstance(trackinfo, list) else [], 1):
            if not isinstance(t, dict) or not t.get("title"):
                continue
            # only compilations carry a per-track artist
            name = f"{t['artist']} - {t['title']}" if t.get("artist") else t["title"]
            tracks.append(f"{t.get('track_num') or n}. {name}")
            if isinstance(t.get("duration"), int | float):
                seconds += t["duration"]
        if not tracks:
            listing = schema.get("track")
            elements = (
                listing.get("itemListElement") if isinstance(listing, dict) else []
            )
            for n, e in enumerate(elements if isinstance(elements, list) else [], 1):
                item = e.get("item") if isinstance(e, dict) else None
                if isinstance(item, dict) and item.get("name"):
                    tracks.append(f"{e.get('position') or n}. {item['name']}")
        return "\n".join(tracks), round(seconds) or None

    def scrape(self):
        assert self.url
        content = BandcampDownloader(self.url).download().html()
        # the embedded data is steadier than the markup, which stays as fallback
        schema = self._embedded_json(
            content, "//script[@type='application/ld+json']/text()"
        )
        tralbum = self._embedded_json(content, "//script[@data-tralbum]/@data-tralbum")
        current = tralbum.get("current")
        current = current if isinstance(current, dict) else {}
        by_artist = schema.get("byArtist")
        by_artist = by_artist if isinstance(by_artist, dict) else {}

        title = str(
            schema.get("name")
            or current.get("title")
            or self.query_str(content, "//h2[@class='trackTitle']/text()")
        ).strip()
        artist = [
            str(
                by_artist.get("name")
                or tralbum.get("artist")
                or self.query_str(content, "//div[@id='name-section']/h3/span/a/text()")
            ).strip()
        ]
        if not title or not artist[0]:
            raise ValueError("given url contains no valid info")

        genre = []  # Bandcamp tags are free-form and mix in locations
        track_list, duration = self._parse_tracks(tralbum, schema)
        release_date = self._parse_date(
            schema.get("datePublished")
            or tralbum.get("album_release_date")
            or current.get("release_date")
        )
        if not release_date:
            release_date = self._parse_date(
                re.sub(
                    r"releas\w+ ",
                    "",
                    self.query_str(
                        content, "//div[@class='tralbumData tralbum-credits']/text()"
                    ),
                )
            )
        # the publisher is the account owner, a label or the artist, and the
        # page does not tell which, so company stays empty
        company = None
        brief = schema.get("description")
        if isinstance(brief, str) and brief.strip():
            brief = brief.replace("\r\n", "\n").strip()
        else:
            brief = "".join(
                content.xpath("//div[@class='tralbumData tralbum-about']/text()")
            )
        cover_url = schema.get("image")
        if not isinstance(cover_url, str) or not cover_url:
            cover_url = self.query_str(content, "//div[@id='tralbumArt']/a/@href")
        bandcamp_album_id = tralbum.get("id") or self._embedded_json(
            content, "//meta[@name='bc-page-properties']/@content"
        ).get("item_id")
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
            "length": duration,
            "company": company,
            "brief": brief,
            "bandcamp_album_id": bandcamp_album_id,
            "cover_image_url": cover_url,
        }
        pd = ResourceContent(metadata=data)
        gtin = upc_to_gtin_13(str(current.get("upc") or ""))
        if gtin:
            pd.lookup_ids[IdType.GTIN] = gtin
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
                albums = cast(
                    list[HtmlElement],
                    h.xpath('//li[@class="searchresult data-search"]'),
                )
                for c in albums:
                    el_cover = cast(list[str], c.xpath('.//div[@class="art"]/img/@src'))
                    cover = el_cover[0] if el_cover else ""
                    el_title = cast(
                        list[str], c.xpath('.//div[@class="heading"]//text()')
                    )
                    title = "".join(el_title).strip() if el_title else "Unknown Title"
                    el_url = cast(
                        list[str], c.xpath('..//div[@class="itemurl"]/a/@href')
                    )
                    url = el_url[0] if el_url else ""
                    el_authors = cast(
                        list[str], c.xpath('.//div[@class="subhead"]//text()')
                    )
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
                _logger.warning("Bandcamp search timeout", extra={"query": q})
                record_search_failure(SiteName.Bandcamp.value, "timeout")
            except Exception as e:
                _logger.error(
                    "Bandcamp search error", extra={"query": q, "exception": e}
                )
                record_search_failure(SiteName.Bandcamp.value, "error")
        return results[offset : offset + page_size]
