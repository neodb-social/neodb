"""
RateYourMusic (rateyourmusic.com).

Public release pages are gated by an aggressive bot challenge, so runtime
fetches go through ScrapDownloader (Scrapfly / Decodo / ScraperAPI /
ScrapingBee / custom — configured via Django settings). Tests use
@use_local_response which short-circuits to local HTML fixtures.

RYM does not expose stable numeric release IDs publicly, so the id_value
is the URL slug path: e.g. `release/album/radiohead/ok-computer`.
"""

import html as html_lib
import json
import logging
import re
from typing import Any, cast

import dateparser

from catalog.common import *
from catalog.models import *
from common.models import normalize_album_types, parse_partial_date
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


RELEASE_TYPES = (
    "album",
    "ep",
    "single",
    "comp",
    "mixtape",
    "video",
    "djmix",
    "bootleg",
    "unauth",
)


class RateYourMusicDownloader(ScrapDownloader):
    def __init__(
        self,
        url: str,
        headers: dict | None = None,
        timeout: float | None = None,
    ):
        super().__init__(url, headers, timeout, "#tracks")

    def validate_response(self, response) -> int:
        if response is None:
            return RESPONSE_NETWORK_ERROR
        if response.status_code != 200:
            return RESPONSE_INVALID_CONTENT
        content = response.content.decode("utf-8", errors="ignore")
        if "page_release" not in content and 'id="tracks"' not in content:
            return RESPONSE_INVALID_CONTENT
        return RESPONSE_OK


@SiteManager.register
class RateYourMusic(AbstractSite):
    SITE_NAME = SiteName.RateYourMusic
    ID_TYPE = IdType.RateYourMusic_Release
    URL_PATTERNS = [
        r"^https?://rateyourmusic\.com/(release/(?:"
        + "|".join(RELEASE_TYPES)
        + r")/[^/?#]+/[^/?#]+)/?",
        r"^https?://www\.rateyourmusic\.com/(release/(?:"
        + "|".join(RELEASE_TYPES)
        + r")/[^/?#]+/[^/?#]+)/?",
    ]
    WIKI_PROPERTY_ID = "P8392"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value: str) -> str:
        return f"https://rateyourmusic.com/{id_value}/"

    def scrape(self) -> ResourceContent:
        assert self.url
        h = RateYourMusicDownloader(self.url).download().html()
        og = self._extract_og(h)
        info = self._extract_info_table(h)

        title = self._extract_title(h) or og.get("og:title", "").split(" by ", 1)[0]
        if not title:
            raise ParseError(self, "title")

        artists = self._extract_artists(h, info)
        genres = self._extract_genres(h)
        release_date = self._parse_release_date(info.get("Released"))
        tracks, total_duration = self._extract_tracks(h)
        company = self._extract_labels(h, og.get("og:description"))
        cover_url = og.get("og:image")
        brief = self._extract_brief(og.get("og:description"))
        album_type = normalize_album_types(info.get("Type"))

        title_lang = detect_language(title)
        localized_title = [{"lang": title_lang, "text": title}]
        localized_description = (
            [{"lang": detect_language(brief), "text": brief}] if brief else []
        )

        data: dict[str, Any] = {
            "title": title,
            "localized_title": localized_title,
            "localized_description": localized_description,
            "brief": brief,
            "artist": artists,
            "genre": genres,
            "track_list": "\n".join(tracks),
            "length": total_duration or None,
            "release_date": release_date,
            "company": company,
            "album_type": album_type,
            "cover_image_url": cover_url,
        }
        pd = ResourceContent(metadata=data)
        links = self._extract_streaming_ids(h)
        spotify_id = links.get("spotify")
        if spotify_id:
            pd.lookup_ids[IdType.Spotify_Album] = spotify_id
        applemusic_id = links.get("applemusic")
        if applemusic_id:
            pd.lookup_ids[IdType.AppleMusic] = applemusic_id
        bandcamp_id = links.get("bandcamp")
        if bandcamp_id:
            pd.lookup_ids[IdType.Bandcamp] = bandcamp_id
        return pd

    @staticmethod
    def _extract_og(h) -> dict[str, str]:
        og: dict[str, str] = {}
        for m in h.xpath("//meta[@property or @name]"):
            key = m.get("property") or m.get("name")
            val = m.get("content")
            if key and val:
                og[key] = val
        return og

    @staticmethod
    def _extract_title(h) -> str | None:
        node = h.xpath("//div[contains(@class, 'album_title')]")
        if not node:
            return None
        # The first text node before the nested <input>/<div> contains the title.
        first = node[0].text
        return first.strip() if first else None

    @staticmethod
    def _extract_info_table(h) -> dict[str, str]:
        info: dict[str, str] = {}
        for row in h.xpath("//th[contains(@class, 'info_hdr')]/ancestor::tr[1]"):
            keys = row.xpath(".//th[contains(@class, 'info_hdr')]/text()")
            key = keys[0].strip() if keys else None
            if not key:
                continue
            value = " ".join(
                t.strip() for t in row.xpath(".//td//text()") if t and t.strip()
            )
            info[key] = value
        return info

    @staticmethod
    def _extract_artists(h, info: dict[str, str]) -> list[str]:
        names = [
            t.strip()
            for t in h.xpath(
                "//tr[th[contains(@class, 'info_hdr')][contains(., 'Artist')]]"
                "//a[contains(@class, 'artist')]/text()"
            )
            if t and t.strip()
        ]
        if not names:
            names = [
                t.strip()
                for t in h.xpath(
                    "//div[contains(@class, 'album_artist_small')]"
                    "//a[contains(@class, 'artist')]/text()"
                )
                if t and t.strip()
            ]
        if not names and "Artist" in info:
            names = [info["Artist"].strip()]
        # de-dupe while preserving order
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    @staticmethod
    def _extract_genres(h) -> list[str]:
        primary = [
            t.strip()
            for t in h.xpath(
                "//span[contains(@class, 'release_pri_genres')]"
                "//a[contains(@class, 'genre')]/text()"
            )
            if t and t.strip()
        ]
        secondary = [
            t.strip()
            for t in h.xpath(
                "//span[contains(@class, 'release_sec_genres')]"
                "//a[contains(@class, 'genre')]/text()"
            )
            if t and t.strip()
        ]
        seen: set[str] = set()
        out: list[str] = []
        for g in primary + secondary:
            if g not in seen:
                seen.add(g)
                out.append(g)
        return out

    @staticmethod
    def _parse_release_date(raw: str | None) -> str | None:
        if not raw:
            return None
        cleaned = re.sub(r"\s+", " ", raw).strip()
        # keep year-only / year-month values at partial precision
        partial = parse_partial_date(cleaned)
        if partial:
            return partial
        try:
            dt = dateparser.parse(
                cleaned,
                settings={
                    "PREFER_DAY_OF_MONTH": "first",
                    "PREFER_MONTH_OF_YEAR": "first",
                },
            )
        except Exception:
            dt = None
        return dt.strftime("%Y-%m-%d") if dt else None

    @staticmethod
    def _extract_tracks(h) -> tuple[list[str], int]:
        lines: list[str] = []
        total = 0
        for li in h.xpath("//ul[@id='tracks']/li[contains(@class, 'track')]"):
            num = "".join(li.xpath(".//span[@class='tracklist_num']//text()")).strip()
            title_parts = li.xpath(
                ".//span[contains(@class, 'tracklist_title')]"
                "//a[contains(@class, 'song')]//text()"
            )
            title = "".join(title_parts).strip()
            if not title:
                continue
            duration_secs = li.xpath(
                ".//span[contains(@class, 'tracklist_duration')]/@data-inseconds"
            )
            duration_txt = "".join(
                li.xpath(".//span[contains(@class, 'tracklist_duration')]//text()")
            ).strip()
            try:
                total += int(duration_secs[0]) if duration_secs else 0
            except TypeError, ValueError:
                pass
            prefix = f"{num}. " if num else ""
            suffix = f" ({duration_txt})" if duration_txt else ""
            lines.append(f"{prefix}{title}{suffix}")
        return lines, total

    @staticmethod
    def _extract_labels(h, og_description: str | None) -> list[str]:
        # The og:description starts with "Released <date> on <Label> (catalog no. ...".
        if og_description:
            m = re.search(
                r"\bReleased\b[^.]*?\bon\s+(.+?)\s*\(catalog no\.",
                og_description,
            )
            if m:
                primary = m.group(1).strip()
                if primary:
                    return [primary]
        # Fallback: first /label/ anchor on the page.
        for t in h.xpath("//a[contains(@href, '/label/')]/text()"):
            s = t.strip()
            if s:
                return [s]
        return []

    @staticmethod
    def _extract_streaming_ids(h) -> dict[str, str]:
        """Parse the data-links JSON on #media_link_button_container_top.

        Schema: {platform: {RymInternalId: {"default": true|absent, "url": "...", ...}}}.
        Returns one external ID per platform — the entry flagged "default" if
        present, else the first. For bandcamp, the `url` field is RYM's stored
        slug (e.g. "kinggizzard.bandcamp.com/album/12-bar-bruise") which already
        matches NeoDB's IdType.Bandcamp value format; for other platforms we
        return the dict key (Spotify base62, Apple Music numeric, etc).
        """
        raw_list = h.xpath(
            "//div[@id='media_link_button_container_top']/@data-links"
        ) or h.xpath("//div[contains(@class, 'media_link_container')]/@data-links")
        if not raw_list:
            return {}
        raw = raw_list[0]
        try:
            data = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError, TypeError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for platform, entries in data.items():
            if not isinstance(entries, dict) or not entries:
                continue
            chosen_key: str | None = None
            chosen_meta: Any = None
            for ext_id, meta in entries.items():
                if isinstance(meta, dict) and meta.get("default"):
                    chosen_key = str(ext_id)
                    chosen_meta = meta
                    break
            if chosen_key is None:
                first_key, first_meta = next(iter(entries.items()))
                chosen_key = str(first_key)
                chosen_meta = first_meta
            if platform == "bandcamp" and isinstance(chosen_meta, dict):
                # Bandcamp's external ID is the URL slug, not RYM's internal id.
                url = cast(dict[str, Any], chosen_meta).get("url")
                if isinstance(url, str) and url:
                    out["bandcamp"] = url
                    continue
            out[str(platform)] = chosen_key
        return out

    @staticmethod
    def _extract_brief(og_description: str | None) -> str:
        if not og_description:
            return ""
        # Strip credit dumps after "Featured peformers:" / "Featured performers:"
        cut = re.split(r"\s*\.\s*Featured pe[rf]+ormers:", og_description, maxsplit=1)
        return cut[0].strip()
