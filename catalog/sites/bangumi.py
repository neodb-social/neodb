import logging
from collections import OrderedDict
from typing import Any

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.models import (
    Album,
    Edition,
    ExternalSearchResultItem,
    Game,
    IdType,
    ItemCategory,
    Movie,
    Performance,
    SiteName,
    TVSeason,
)
from catalog.models.game import GameReleaseType
from catalog.models.utils import detect_isbn_asin
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


@SiteManager.register
class Bangumi(AbstractSite):
    SITE_NAME = SiteName.Bangumi
    ID_TYPE = IdType.Bangumi
    URL_PATTERNS = [
        r"https://bgm\.tv/subject/(\d+)",
        r"https://bangumi\.tv/subject/(\d+)",
        r"https://chii\.in/subject/(\d+)",
    ]
    WIKI_PROPERTY_ID = ""
    MATCHABLE_MODELS = [Performance, TVSeason, Movie, Edition, Album, Game]

    @classmethod
    def get_category(
        cls, o: dict[str, Any], fetch_resources: bool = False
    ) -> tuple[ItemCategory, dict[str, Any]]:
        dt = o.get("date")
        pub_year = None
        pub_month = None
        release_date = None
        release_type = None
        showtime = None
        year = None
        related_resources = []
        match o["type"]:
            case 1:
                model = "Edition"
                category = ItemCategory.Book
                if o["series"] and fetch_resources:
                    # model = "Series" TODO
                    res = (
                        BasicDownloader(
                            f"https://api.bgm.tv/v0/subjects/{o['id']}/subjects",
                            headers={
                                "User-Agent": settings.NEODB_USER_AGENT,
                            },
                        )
                        .download()
                        .json()
                    )

                    for s in res:
                        if s["relation"] != "单行本":
                            continue
                        related_resources.append(
                            {
                                "url": cls.id_to_url(s["id"]),
                            }
                        )
                if dt:
                    d = dt.split("-")
                    pub_year = d[0]
                    pub_month = d[1]
            case 2 | 6:
                is_season = o["platform"] in {
                    "TV",
                    "OVA",  # may be movie in other sites
                    "WEB",
                    "电视剧",
                    "欧美剧",
                    "日剧",
                    "华语剧",
                    "综艺",
                }
                category = ItemCategory.TV if is_season else ItemCategory.Movie
                model = "TVSeason" if is_season else "Movie"
                if "舞台剧" in [
                    t["name"] for t in o["tags"]
                ]:  # 只能这样判断舞台剧了，bangumi三次元分类太少
                    category = ItemCategory.Performance
                    model = "Performance"
                if dt:
                    year = dt.split("-")[0]
                    showtime = [
                        {"time": dt, "region": "首播日期" if is_season else "发布日期"}
                    ]
            case 3:
                model = "Album"
                category = ItemCategory.Music
            case 4:
                model = "Game"
                category = ItemCategory.Game
                match o["platform"]:
                    case "游戏":
                        release_type = GameReleaseType.GAME
                    case "扩展包":
                        release_type = GameReleaseType.DLC
                if dt:
                    release_date = dt
            case _:
                raise ValueError(
                    f"Unknown type {o['type']} for bangumi subject {o['id']}"
                )
        return category, {
            "preferred_model": model,
            "related_resources": related_resources,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "release_date": release_date,
            "release_type": release_type,
            "showtime": showtime,
            "year": year,
        }

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://bgm.tv/subject/{id_value}"

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        results = []
        bgm_type = {
            "all": None,
            "movietv": [2, 6],
            "movie": [2, 6],
            "tv": [2, 6],
            "book": [1],
            "game": [4],
            "performance": [6],
            "music": [3],
        }
        if category not in bgm_type:
            return results
        search_url = f"https://api.bgm.tv/v0/search/subjects?limit={page_size}&offset={(page - 1) * page_size}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    search_url,
                    headers={"User-Agent": settings.NEODB_USER_AGENT},
                    json={"keyword": q, "filter": {"type": bgm_type[category]}},
                    timeout=2,
                )
                if response.status_code == 200:
                    r = response.json()
                    for s in r["data"]:
                        cat, _ = cls.get_category(s)
                        results.append(
                            ExternalSearchResultItem(
                                category=cat,
                                source_site=cls.SITE_NAME,
                                source_url=cls.id_to_url(s["id"]),
                                title=s["name"],
                                subtitle="",
                                brief=s.get("summary", ""),
                                cover_url=s["images"].get("common"),
                            )
                        )
            except httpx.ReadTimeout:
                logger.warning("Bangumi search timeout", extra={"query": q})
            except Exception as e:
                logger.error("Bangumi search error", extra={"query": q, "exception": e})
        return results

    def scrape(self):
        api_url = f"https://api.bgm.tv/v0/subjects/{self.id_value}"
        o = (
            BasicDownloader(
                api_url,
                headers={
                    "User-Agent": settings.NEODB_USER_AGENT,
                },
            )
            .download()
            .json()
        )
        category, data = self.get_category(o, True)
        title = o.get("name_cn") or o.get("name")
        orig_title = o.get("name") if o.get("name") != title else None
        brief = o.get("summary")
        episodes = o.get("total_episodes", 0)
        genre = None
        platform = None
        other_title = []
        imdb_code = None
        isbn_type = None
        isbn = None
        language = None
        pub_house = None
        orig_creator = None
        authors = []
        site = None
        director = None
        playwright = None
        actor = None
        pages = None
        price = None
        opening_date = None
        closing_date = None
        location = None
        for i in o.get("infobox", []):
            k = i["key"].lower()
            v = i["value"]
            match k:
                case "别名":
                    other_title = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "话数":
                    try:
                        episodes = int(v)
                    except ValueError:
                        pass
                case "imdb_id":
                    imdb_code = v
                case "isbn":
                    isbn_type, isbn = detect_isbn_asin(
                        v[0]["v"] if isinstance(v, list) else v
                    )
                case "语言":
                    language = v.split("、") if v else []
                case "出版社":
                    pub_house = v
                case "导演":
                    director = v.split("、") if v else []
                case "编剧" | "脚本":
                    playwright = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "原作":
                    match category:
                        case ItemCategory.Book:
                            authors.append(v)
                        case ItemCategory.Performance:
                            orig_creator = (
                                [d["v"] for d in v]
                                if isinstance(v, list)
                                else ([v] if isinstance(v, str) else [])
                            )
                case "作画":
                    authors.append(v)
                case "作者":
                    authors.extend(
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "平台":
                    platform = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "游戏类型" | "类型":
                    genre = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "官方网站" | "website":
                    site = (
                        v[0]["v"]
                        if isinstance(v, list)
                        else (v if isinstance(v, str) else None)
                    )
                case "页数":
                    pages = v
                case "价格":
                    price = v
                case "开始":
                    opening_date = v
                case "结束":
                    closing_date = v
                case "演出":
                    if category == ItemCategory.Performance:
                        director = v.split("、") if v else []
                case "主演":
                    if category == ItemCategory.Performance:
                        actor = (
                            [{"name": d["v"], "role": None} for d in v]
                            if isinstance(v, list)
                            else (
                                [{"name": w, "role": None} for w in v.split("、")]
                                if isinstance(v, str)
                                else []
                            )
                        )
                case "会场" | "演出地点":
                    location = v.split("、") if v else []

        img_url = o["images"].get("large") or o["images"].get("common")
        raw_img = None
        ext = None
        if img_url:
            raw_img, ext = BasicImageDownloader.download_image(
                img_url, None, headers={}
            )
        titles = OrderedDict.fromkeys(
            [title] + (other_title or []) + ([orig_title] if orig_title else [])
        )
        if o.get("name_cn"):
            titles[o.get("name_cn")] = "zh-cn"
        localized_title = [
            {"lang": lang or detect_language(t), "text": t}
            for t, lang in titles.items()
        ]
        localized_desc = (
            [{"lang": detect_language(brief), "text": brief}] if brief else []
        )
        data.update(
            {
                "localized_title": localized_title,
                "localized_description": localized_desc,
                "title": title,
                "orig_title": orig_title,
                "other_title": other_title or None,
                "orig_creator": orig_creator,
                "author": authors,
                "genre": genre,
                "translator": None,
                "director": director,
                "playwright": playwright,
                "actor": actor,
                "language": language,
                "platform": platform,
                "imdb_code": imdb_code,
                "pub_house": pub_house,
                "binding": None,
                "episode_count": episodes or None,
                "official_site": site,
                "site": site,
                "isbn": isbn,
                "brief": brief,
                "cover_image_url": img_url,
                "pages": pages,
                "price": price,
                "opening_date": opening_date,
                "closing_date": closing_date,
                "location": location,
            }
        )
        lookup_ids = {}
        if isbn:
            lookup_ids[isbn_type] = isbn
        if imdb_code:
            lookup_ids[IdType.IMDB] = imdb_code
        return ResourceContent(
            metadata={k: v for k, v in data.items() if v is not None},
            cover_image=raw_img,
            cover_image_extention=ext,
            lookup_ids=lookup_ids,
        )
