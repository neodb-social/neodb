import logging
from collections import OrderedDict

from django.conf import settings

from catalog.book.utils import detect_isbn_asin
from catalog.common import *
from catalog.game.models import GameReleaseType
from catalog.models import *
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
    DEFAULT_MODEL = None

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://bgm.tv/subject/{id_value}"

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
        showtime = None
        pub_year = None
        pub_month = None
        year = None
        dt = o.get("date")
        release_type = None
        related_resources = []
        match o["type"]:
            case 1:
                if o["series"]:
                    model = "Work"
                    # model = "Series" TODO Series
                    res = (
                        BasicDownloader(
                            f"https://api.bgm.tv/v0/subjects/{self.id_value}/subjects",
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
                                "url": Bangumi.id_to_url(s["id"]),
                            }
                        )
                model = "Edition"
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
                model = "TVSeason" if is_season else "Movie"
                if "舞台剧" in [
                    t["name"] for t in o["tags"]
                ]:  # 只能这样判断舞台剧了，bangumi三次元分类太少
                    model = "Performance"
                if dt:
                    year = dt.split("-")[0]
                    showtime = [
                        {"time": dt, "region": "首播日期" if is_season else "发布日期"}
                    ]
            case 3:
                model = "Album"
            case 4:
                model = "Game"
                match o["platform"]:
                    case "游戏":
                        release_type = GameReleaseType.GAME
                    case "扩展包":
                        release_type = GameReleaseType.DLC
            case _:
                raise ValueError(
                    f"Unknown type {o['type']} for bangumi subject {self.id_value}"
                )
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
                    isbn_type, isbn = detect_isbn_asin(v)
                case "语言":
                    language = v
                case "出版社":
                    pub_house = v
                case "导演":
                    director = v
                case "编剧" | "脚本":
                    playwright = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "原作":
                    match model:
                        case "Edition":
                            authors.append(v)
                        case "Performance":
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
                    site = v[0] if isinstance(v, list) else v
                case "页数":
                    pages = v
                case "价格":
                    price = v
                case "开始":
                    opening_date = v
                case "结束":
                    closing_date = v
                case "演出":
                    if model == "Performance":
                        director = v
                case "主演":
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
                    location = v

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
        data = {
            "localized_title": localized_title,
            "localized_description": localized_desc,
            "preferred_model": model,
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
            "release_type": release_type,
            "year": year,
            "showtime": showtime,
            "imdb_code": imdb_code,
            "pub_house": pub_house,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "binding": None,
            "episode_count": episodes or None,
            "official_site": site,
            "site": site,
            "isbn": isbn,
            "brief": brief,
            "cover_image_url": img_url,
            "release_date": dt,
            "pages": pages,
            "price": price,
            "opening_date": opening_date,
            "closing_date": closing_date,
            "location": location,
            "related_resources": related_resources,
        }
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
