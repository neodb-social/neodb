import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import dateparser

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


def _uniq(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for v in values:
        if not v:
            continue
        if v in seen:
            continue
        seen.add(v)
        result.append(v)
    return result


@SiteManager.register
class Itch(AbstractSite):
    SITE_NAME = SiteName.Itch
    ID_TYPE = IdType.Itch
    URL_PATTERNS = [
        r"^https?://([a-z0-9\-]+\.itch\.io/[^/?#]+).*",
        r"^https?://itch\.io/embed/(\d+).*",
        r"^https?://itch\.io/game/(\d+).*",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value: str):
        return f"https://{id_value}"

    @classmethod
    def url_to_id(cls, url: str):
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host:
            return None
        path = parsed.path.strip("/")
        if host.endswith(".itch.io"):
            slug = path.split("/")[0] if path else ""
            return f"{host}/{slug}" if slug else None
        if host == "itch.io":
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] in ("embed", "game"):
                return f"{host}/{parts[0]}/{parts[1]}"
        slug = path.split("/")[0] if path else ""
        return f"{host}/{slug}" if slug else host

    @classmethod
    def _extract_meta(cls, content, xpath: str) -> str | None:
        try:
            val = content.xpath(xpath)
            if val:
                return val[0].strip()
        except Exception:
            return None
        return None

    @classmethod
    def _extract_canonical(cls, content) -> str | None:
        return (
            cls._extract_meta(content, "//link[@rel='canonical']/@href")
            or cls._extract_meta(content, "//meta[@property='og:url']/@content")
            or cls._extract_meta(content, "//meta[@name='og:url']/@content")
        )

    @classmethod
    def _extract_json_ld(cls, content) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        scripts = content.xpath("//script[@type='application/ld+json']/text()")
        for txt in scripts:
            if not txt or not txt.strip():
                continue
            try:
                data = json.loads(txt)
            except Exception:
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        results.append(item)
                continue
            if isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    for item in data["@graph"]:
                        if isinstance(item, dict):
                            results.append(item)
                else:
                    results.append(data)
        return results

    @classmethod
    def _extract_game_id_from_text(cls, text: str) -> str | None:
        if not text:
            return None
        patterns = [
            r'data-game_id="(\d+)"',
            r'data-game-id="(\d+)"',
            r'"game_id"\s*:\s*(\d+)',
            r"game_id\s*=\s*(\d+)",
            r"itch\.io/embed/(\d+)",
            r"itch\.io/game/(\d+)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1)
        return None

    @classmethod
    def _extract_game_id_from_json_ld(cls, items: list[dict[str, Any]]) -> str | None:
        for item in items:
            for key in ("identifier", "url", "@id"):
                val = item.get(key)
                if isinstance(val, str):
                    gid = cls._extract_game_id_from_text(val)
                    if gid:
                        return gid
                if isinstance(val, dict):
                    val2 = val.get("value") or val.get("@id") or val.get("url")
                    if isinstance(val2, str):
                        gid = cls._extract_game_id_from_text(val2)
                        if gid:
                            return gid
        return None

    @classmethod
    def _extract_people(cls, val: Any) -> list[str]:
        if not val:
            return []
        if isinstance(val, str):
            return [val]
        if isinstance(val, dict):
            name = val.get("name")
            return [name] if name else []
        if isinstance(val, list):
            names = []
            for item in val:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict) and item.get("name"):
                    names.append(item["name"])
            return names
        return []

    @classmethod
    def _extract_platforms(cls, val: Any) -> list[str]:
        if not val:
            return []
        if isinstance(val, str):
            return [val]
        if isinstance(val, list):
            return [str(v) for v in val if v]
        return []

    @classmethod
    def _probe_itch_page(cls, url: str) -> dict[str, str | None]:
        info: dict[str, str | None] = {"game_id": None, "canonical_url": None}
        try:
            resp = BasicDownloader2(url, timeout=2).download()
            content = resp.html()
            html_text = resp.text or ""
        except Exception:
            return info
        info["canonical_url"] = cls._extract_canonical(content)
        info["game_id"] = cls._extract_game_id_from_text(html_text)
        return info

    @classmethod
    def validate_url_fallback(cls, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc.lower()
        if host.endswith(".itch.io") or host == "itch.io":
            return False
        info = cls._probe_itch_page(url)
        if info.get("canonical_url"):
            return True
        return bool(info.get("game_id"))

    def scrape(self):
        if not self.url:
            raise ParseError(self, "url")

        resp = BasicDownloader2(self.url).download()
        content = resp.html()
        html_text = resp.text or ""

        canonical_url = self._extract_canonical(content)

        json_ld_items = self._extract_json_ld(content)
        json_ld_game: dict[str, Any] | None = None
        for item in json_ld_items:
            typ = item.get("@type") or item.get("type")
            types = []
            if isinstance(typ, list):
                types = [str(t).lower() for t in typ]
            elif isinstance(typ, str):
                types = [typ.lower()]
            if any("game" in t for t in types):
                json_ld_game = item
                break

        title = (
            (json_ld_game.get("name") if json_ld_game else None)
            or self._extract_meta(content, "//meta[@property='og:title']/@content")
            or self._extract_meta(content, "//meta[@name='twitter:title']/@content")
            or self._extract_meta(content, "//title/text()")
        )
        title = title.strip() if title else None
        if not title:
            raise ParseError(self, "title")

        description = (
            (json_ld_game.get("description") if json_ld_game else None)
            or self._extract_meta(content, "//meta[@property='og:description']/@content")
            or self._extract_meta(content, "//meta[@name='description']/@content")
        )
        description = description.strip() if description else ""

        cover_url = (
            (json_ld_game.get("image") if json_ld_game else None)
            or self._extract_meta(content, "//meta[@property='og:image']/@content")
            or self._extract_meta(content, "//meta[@name='twitter:image']/@content")
        )
        if isinstance(cover_url, list):
            cover_url = cover_url[0] if cover_url else None
        elif isinstance(cover_url, dict):
            cover_url = cover_url.get("url") or cover_url.get("@id")

        release_date = None
        if json_ld_game and json_ld_game.get("datePublished"):
            dt = dateparser.parse(str(json_ld_game.get("datePublished")))
            release_date = dt.strftime("%Y-%m-%d") if dt else None

        platforms = []
        if json_ld_game:
            platforms = self._extract_platforms(
                json_ld_game.get("gamePlatform")
                or json_ld_game.get("operatingSystem")
            )

        genre = []
        if json_ld_game and json_ld_game.get("genre"):
            if isinstance(json_ld_game.get("genre"), list):
                genre = [str(g) for g in json_ld_game.get("genre") if g]
            elif isinstance(json_ld_game.get("genre"), str):
                genre = [str(json_ld_game.get("genre"))]

        keywords = self._extract_meta(content, "//meta[@name='keywords']/@content")
        if keywords:
            genre.extend([k.strip() for k in keywords.split(",") if k.strip()])

        tag_nodes = content.xpath("//a[contains(@class,'tag')]/text()")
        if tag_nodes:
            genre.extend([t.strip() for t in tag_nodes if t.strip()])

        genre = _uniq(genre)

        author = []
        if json_ld_game:
            author = self._extract_people(
                json_ld_game.get("author") or json_ld_game.get("creator")
            )

        localized_title = (
            [{"lang": detect_language(title), "text": title}] if title else []
        )
        localized_desc = (
            [{"lang": detect_language(description), "text": description}]
            if description
            else []
        )

        pd = ResourceContent(
            metadata={
                "localized_title": localized_title,
                "localized_description": localized_desc,
                "title": title,
                "brief": description,
                "developer": author,
                "publisher": author,
                "release_date": release_date,
                "genre": genre,
                "platform": platforms,
                "official_site": canonical_url or self.url,
                "cover_image_url": cover_url,
            }
        )

        game_id = self._extract_game_id_from_json_ld(json_ld_items) or self._extract_game_id_from_text(
            html_text
        )
        if game_id:
            pd.lookup_ids[IdType.ItchGameId] = game_id
        if canonical_url:
            canonical_id = self.url_to_id(canonical_url)
            if canonical_id:
                pd.lookup_ids[IdType.Itch] = canonical_id
        return pd
