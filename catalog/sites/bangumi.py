import logging

from catalog.book.utils import detect_isbn_asin
from catalog.common import *
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
        o = BasicDownloader(api_url).download().json()
        showtime = None
        pub_year = None
        pub_month = None
        year = None
        dt = o.get("date")
        episodes = o.get("total_episodes", 0)
        match o["type"]:
            case 1:
                model = "Edition"
                if dt:
                    d = dt.split("-")
                    pub_year = d[0]
                    pub_month = d[1]
            case 2 | 6:
                is_series = episodes > 1
                model = "TVSeason" if is_series else "Movie"
                if dt:
                    year = dt.split("-")[0]
                    showtime = [
                        {"time": dt, "region": "首播日期" if is_series else "发布日期"}
                    ]
            case 3:
                model = "Album"
            case 4:
                model = "Game"
            case _:
                raise ValueError(
                    f"Unknown type {o['type']} for bangumi subject {self.id_value}"
                )
        title = o.get("name_cn") or o.get("name")
        orig_title = o.get("name") if o.get("name") != title else None
        brief = o.get("summary")

        genre = None
        platform = None
        other_title = []
        imdb_code = None
        isbn_type = None
        isbn = None
        language = None
        pub_house = None
        authors = None
        site = None
        director = None
        pages = None
        price = None
        for i in o.get("infobox", []):
            k = i["key"]
            v = i["value"]
            match k:
                case "别名":
                    other_title = (
                        [d["v"] for d in v]
                        if isinstance(v, list)
                        else ([v] if isinstance(v, str) else [])
                    )
                case "imdb_id":
                    imdb_code = v
                case "isbn" | "ISBN":
                    isbn_type, isbn = detect_isbn_asin(v)
                case "语言":
                    language = v
                case "出版社":
                    pub_house = v
                case "导演":
                    director = v
                case "作者":
                    authors = (
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
                case "游戏类型":
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

        img_url = o["images"].get("large") or o["images"].get("common")
        raw_img = None
        ext = None
        if img_url:
            raw_img, ext = BasicImageDownloader.download_image(
                img_url, None, headers={}
            )
        titles = set(
            [title] + (other_title or []) + ([orig_title] if orig_title else [])
        )
        localized_title = [{"lang": detect_language(t), "text": t} for t in titles]
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
            "author": authors,
            "genre": genre,
            "translator": None,
            "director": director,
            "language": language,
            "platform": platform,
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
