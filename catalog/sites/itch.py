import json
import re
from typing import Any, Iterable, cast
from urllib.parse import urlparse

import dateparser
from lxml import etree
from lxml.html import HtmlElement

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language
from journal.models.renderers import html_to_text


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
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value: str):
        if id_value.startswith("games/"):
            game_id = id_value.split("/", 1)[1]
            return f"https://itch.io/embed/{game_id}"
        if id_value.startswith("embed/"):
            embed_id = id_value.split("/", 1)[1]
            return f"https://itch.io/embed/{embed_id}"
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
            if len(parts) >= 2 and parts[0] == "embed":
                return f"{parts[0]}/{parts[1]}"
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
    def _extract_itch_path(cls, content) -> str | None:
        return cls._extract_meta(content, "//meta[@name='itch:path']/@content")

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
            r'<meta[^>]+name=["\']itch:path["\'][^>]+content=["\']([^"\']+)["\']',
            r'data-game_id="(\d+)"',
            r'data-game-id="(\d+)"',
            r'"game_id"\s*:\s*(\d+)',
            r"game_id\s*=\s*(\d+)",
            r"itch\.io/embed/(\d+)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1)
        return None

    @classmethod
    def _normalize_game_id(cls, game_id: str | None) -> str | None:
        if not game_id:
            return None
        gid = game_id.strip()
        if re.fullmatch(r"\d+", gid):
            return f"games/{gid}"
        return gid

    @classmethod
    def _extract_any_game_url(cls, text: str) -> str | None:
        if not text:
            return None
        m = re.search(r"https?://[A-Za-z0-9\\-]+\\.itch\\.io/[^\\s\"'<>?#]+", text)
        if not m:
            return None
        u = m.group(0)
        parsed = urlparse(u)
        host = parsed.netloc.lower()
        slug = parsed.path.strip("/").split("/")[0] if parsed.path else ""
        if host and slug:
            return f"https://{host}/{slug}"
        return None

    @classmethod
    def _normalize_embed_button_text(cls, text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    @classmethod
    def _extract_embed_target_url(cls, content) -> str | None:
        anchors = content.xpath("//a[@href]")
        for a in anchors:
            try:
                text = a.text_content() or ""
            except Exception:
                continue
            norm = cls._normalize_embed_button_text(text)
            if (
                "itchio" in norm
                and "onitchio" in norm
                and ("download" in norm or "play" in norm)
            ):
                href = a.get("href")
                if href:
                    return href.strip()
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
    def _extract_platforms_from_links(cls, content) -> list[str]:
        platform_by_path = {
            "/games/html5": "Web",
            "/games/platform-windows": "Windows",
            "/games/platform-osx": "macOS",
            "/games/platform-linux": "Linux",
            "/games/platform-android": "Android",
            "/games/platform-ios": "iOS",
        }
        hrefs = content.xpath("//a[@href]/@href")
        paths = []
        for href in hrefs:
            if not isinstance(href, str):
                continue
            parsed = urlparse(href)
            path = parsed.path.rstrip("/")
            if path:
                paths.append(path)
        platforms = []
        for path, name in platform_by_path.items():
            if path.rstrip("/") in paths:
                platforms.append(name)
        return platforms

    @classmethod
    def _extract_table_row(cls, content, label: str):
        rows = content.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), ' game_info_panel_widget ')]"
            "//table//tr"
        )
        for row in rows:
            cells = row.xpath("./td")
            if len(cells) < 2:
                continue
            cell_label = "".join(cells[0].xpath(".//text()")).strip()
            if cell_label == label:
                return cells[1]
        return None

    @classmethod
    def _download_page(cls, url: str):
        try:
            resp = BasicDownloader2(url, timeout=2).download()
            return resp.html(), (resp.text or "")
        except Exception:
            return None, ""

    @classmethod
    def _probe_itch_page_with_content(cls, url: str):
        info: dict[str, str | None] = {"game_id": None, "canonical_url": None}
        content, html_text = cls._download_page(url)
        if not content:
            return info, None, ""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host == "itch.io" and parsed.path.startswith("/embed/"):
            info["canonical_url"] = cls._extract_embed_target_url(content)
        else:
            info["canonical_url"] = cls._extract_canonical(
                content
            ) or cls._extract_any_game_url(html_text)
        info["game_id"] = cls._normalize_game_id(
            cls._extract_itch_path(content) or cls._extract_game_id_from_text(html_text)
        )
        return info, content, html_text

    @classmethod
    def _parse_release_date(cls, text: str | None) -> str | None:
        if not text:
            return None
        cleaned = text.strip()
        if "@" in cleaned:
            cleaned = cleaned.split("@", 1)[0].strip()
        dt = dateparser.parse(cleaned, languages=["en"])
        if not dt:
            dt = dateparser.parse(cleaned)
        return dt.strftime("%Y-%m-%d") if dt else None

    @classmethod
    def _platforms_from_traits(cls, traits: Iterable[str]) -> list[str]:
        trait_map = {
            "p_windows": "Windows",
            "p_osx": "macOS",
            "p_linux": "Linux",
            "p_android": "Android",
            "p_ios": "iOS",
            "p_html": "Web",
            "p_web": "Web",
        }
        platforms = []
        for trait in traits:
            name = trait_map.get(trait)
            if name:
                platforms.append(name)
        return platforms

    @classmethod
    def _fetch_api_game(cls, game_id: str) -> dict[str, Any] | None:
        if not game_id or not game_id.startswith("games/"):
            return None
        numeric_id = game_id.split("/", 1)[1]
        if not numeric_id.isdigit():
            return None
        # NOTE: API access may require verification; keep code here for later enablement.
        # api_url = f"https://api.itch.io/games/{numeric_id}"
        # headers = {
        #     "Accept": "application/json",
        #     "User-Agent": settings.NEODB_USER_AGENT,
        # }
        # logger.info("Itch API fetch", extra={"url": api_url})
        # dl = BasicDownloader(api_url, headers=headers)
        # resp, response_type = dl._download(api_url)
        # if response_type != RESPONSE_OK or not resp:
        #     status = getattr(resp, "status_code", None)
        #     body = getattr(resp, "text", "") if resp else ""
        #     logger.warning(
        #         "Itch API request failed (status={status}, response_type={rtype}, url={url}, body={body})",
        #         status=status,
        #         rtype=response_type,
        #         url=api_url,
        #         body=(body[:200] if body else ""),
        #     )
        #     return None
        # try:
        #     return resp.json()
        # except Exception:
        #     body = getattr(resp, "text", "")
        #     logger.warning(
        #         "Itch API response not JSON",
        #         extra={"url": api_url, "body": (body[:200] if body else "")},
        #     )
        #     return None
        return None

    @classmethod
    def _probe_itch_page(cls, url: str) -> dict[str, str | None]:
        info, _, _ = cls._probe_itch_page_with_content(url)
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

    def get_resource_ready(
        self,
        auto_save=True,
        auto_create=True,
        auto_link=True,
        preloaded_content=None,
        ignore_existing_content=False,
    ) -> ExternalResource | None:
        if self.url:
            parsed = urlparse(self.url)
            host = parsed.netloc.lower()
            info, content, html_text = self._probe_itch_page_with_content(self.url)
            self._preloaded_content = content
            self._preloaded_html_text = html_text
            canonical_url = info.get("canonical_url")
            game_id = info.get("game_id")
            if host == "itch.io" and parsed.path.startswith("/embed/"):
                if not canonical_url:
                    return None
                canonical_site = SiteManager.get_site_by_url(
                    canonical_url, detect_redirection=False, detect_fallback=False
                )
                if canonical_site and canonical_site.ID_TYPE == self.ID_TYPE:
                    return canonical_site.get_resource_ready(
                        auto_save=auto_save,
                        auto_create=auto_create,
                        auto_link=auto_link,
                        preloaded_content=preloaded_content,
                        ignore_existing_content=ignore_existing_content,
                    )
                return None
            if canonical_url:
                self.url = canonical_url
            if game_id:
                self.id_value = game_id
            else:
                return None
        return super().get_resource_ready(
            auto_save=auto_save,
            auto_create=auto_create,
            auto_link=auto_link,
            preloaded_content=preloaded_content,
            ignore_existing_content=ignore_existing_content,
        )

    def scrape(self):
        if not self.url:
            raise ParseError(self, "url")

        content = getattr(self, "_preloaded_content", None)
        html_text = getattr(self, "_preloaded_html_text", "")
        if not content:
            resp = BasicDownloader2(self.url).download()
            content = resp.html()
            html_text = resp.text or ""

        parsed = urlparse(self.url)
        host = parsed.netloc.lower()
        if host == "itch.io" and parsed.path.startswith("/embed/"):
            canonical_url = self._extract_embed_target_url(content)
        else:
            canonical_url = self._extract_canonical(
                content
            ) or self._extract_any_game_url(html_text)

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
            self._extract_meta(
                content,
                "//div[@id='header' and contains(concat(' ', normalize-space(@class), ' '), ' header ')]"
                "//h1[contains(concat(' ', normalize-space(@class), ' '), ' game_title ')]/text()",
            )
            or (json_ld_game.get("name") if json_ld_game else None)
            or self._extract_meta(content, "//meta[@property='og:title']/@content")
            or self._extract_meta(content, "//meta[@name='twitter:title']/@content")
            or self._extract_meta(content, "//title/text()")
        )
        title = title.strip() if title else None

        description = (
            (json_ld_game.get("description") if json_ld_game else None)
            or self._extract_meta(
                content, "//meta[@property='og:description']/@content"
            )
            or self._extract_meta(content, "//meta[@name='description']/@content")
        )
        description = description.strip() if description else ""
        desc_blocks = cast(
            list[HtmlElement],
            content.xpath(
                "//div[contains(concat(' ', normalize-space(@class), ' '), ' formatted_description ')"
                " and contains(concat(' ', normalize-space(@class), ' '), ' user_formatted ')]"
            ),
        )
        if desc_blocks:
            try:
                html_fragment = etree.tostring(
                    desc_blocks[0], encoding="unicode", method="html"
                )
                body_text = html_to_text(html_fragment).strip()
                if body_text:
                    description = (
                        description + "\n\n" + body_text if description else body_text
                    )
            except Exception:
                pass
        if description:
            description = description.replace("\r\n", "\n").replace("\r", "\n")
            description = re.sub(r"[ \t]+\n", "\n", description)
            description = re.sub(r"\n{3,}", "\n\n", description).strip()

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
                json_ld_game.get("gamePlatform") or json_ld_game.get("operatingSystem")
            )
        platforms += self._extract_platforms_from_links(content)

        author = []
        if json_ld_game:
            author = self._extract_people(
                json_ld_game.get("author") or json_ld_game.get("creator")
            )

        genre = []
        if json_ld_game:
            genre_val = json_ld_game.get("genre")
            if isinstance(genre_val, list):
                genre = [str(g) for g in genre_val if g]
            elif isinstance(genre_val, str):
                genre = [genre_val]

        published_cell = self._extract_table_row(content, "Published")
        if published_cell is not None and not release_date:
            published_title = self._extract_meta(published_cell, ".//abbr/@title")
            published_text = "".join(published_cell.xpath(".//text()")).strip()
            release_date = (
                self._parse_release_date(published_title)
                or self._parse_release_date(published_text)
                or release_date
            )

        authors_cell = self._extract_table_row(content, "Authors")
        if authors_cell is not None:
            author_names = [
                t.strip()
                for t in authors_cell.xpath(".//a/text()")
                if isinstance(t, str) and t.strip()
            ]
            if author_names:
                author = _uniq(author + author_names)

        genre_cell = self._extract_table_row(content, "Genre")
        if genre_cell is not None:
            genre_names = [
                t.strip()
                for t in genre_cell.xpath(".//a/text()")
                if isinstance(t, str) and t.strip()
            ]
            if genre_names:
                genre = _uniq(genre + genre_names)

        tags_cell = self._extract_table_row(content, "Tags")
        if tags_cell is not None:
            tag_names = [
                t.strip()
                for t in tags_cell.xpath(".//a/text()")
                if isinstance(t, str) and t.strip()
            ]
            if tag_names:
                genre = _uniq(genre + tag_names)

        keywords = self._extract_meta(content, "//meta[@name='keywords']/@content")
        if keywords:
            genre.extend([k.strip() for k in keywords.split(",") if k.strip()])

        tag_nodes = cast(
            list[str],
            content.xpath("//a[contains(@class,'tag')]/text()"),
        )
        if tag_nodes:
            genre.extend(
                [t.strip() for t in tag_nodes if isinstance(t, str) and t.strip()]
            )

        genre = _uniq(genre)

        game_id = self._normalize_game_id(
            self._extract_itch_path(content)
            or self._extract_game_id_from_json_ld(json_ld_items)
            or self._extract_game_id_from_text(html_text)
        )
        if not game_id:
            raise ParseError(self, "itch:path")

        api_data = self._fetch_api_game(game_id)
        if api_data and isinstance(api_data, dict) and api_data.get("game"):
            game = api_data.get("game") or {}
            api_title = game.get("title")
            api_desc = game.get("short_text")
            if api_title:
                title = api_title
            if api_desc:
                description = api_desc
            user = game.get("user") or {}
            display_name = user.get("display_name")
            username = user.get("username")
            author = _uniq([display_name or "", username or ""])
            api_traits = game.get("traits") or []
            if isinstance(api_traits, list):
                platforms += self._platforms_from_traits(api_traits)
            api_cover = game.get("cover_url") or game.get("still_cover_url")
            if api_cover:
                cover_url = api_cover
            if not canonical_url and game.get("url"):
                canonical_url = game.get("url")
            if not release_date and game.get("published_at"):
                dt = dateparser.parse(str(game.get("published_at")))
                release_date = dt.strftime("%Y-%m-%d") if dt else None
        else:
            if not title:
                raise ParseError(self, "title")

        platforms = _uniq(platforms)

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

        pd.lookup_ids[IdType.Itch] = game_id
        return pd
