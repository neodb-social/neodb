"""
The Movie Database


these language code from TMDB are not in currently iso-639-1
{'iso_639_1': 'xx', 'english_name': 'No Language', 'name': 'No Language'}
{'iso_639_1': 'sh', 'english_name': 'Serbo-Croatian', 'name': ''} - deprecated for several
{'iso_639_1': 'mo', 'english_name': 'Moldavian', 'name': ''} - deprecated for ro-MD
{'iso_639_1': 'cn', 'english_name': 'Cantonese', 'name': '粤语'} - faked for yue

"""

import logging
import re
from urllib.parse import quote_plus

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.movie.models import *
from catalog.tv.models import *
from common.models.lang import SITE_PREFERRED_LANGUAGES

from .douban import *

_logger = logging.getLogger(__name__)


def _get_language_code():
    match settings.LANGUAGE_CODE:
        case "zh-hans":
            return "zh-CN"
        case "zh-hant":
            return "zh-TW"
        case _:
            return "en-US"


def _get_preferred_languages():
    langs = {}
    for lang in SITE_PREFERRED_LANGUAGES:
        if lang == "zh":
            langs.update({"zh-cn": "zh-CN", "zh-tw": "zh-TW", "zh-hk": "zh-HK"})
        else:
            langs[lang] = lang
    return langs


TMDB_DEFAULT_LANG = _get_language_code()
TMDB_PREFERRED_LANGS = _get_preferred_languages()


def search_tmdb_by_imdb_id(imdb_id):
    tmdb_api_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={settings.TMDB_API3_KEY}&language={TMDB_DEFAULT_LANG}&external_source=imdb_id"
    try:
        return BasicDownloader(tmdb_api_url).download().json()
    except Exception:
        return {}


def query_tmdb_tv_episode(tv, season, episode):
    tmdb_api_url = f"https://api.themoviedb.org/3/tv/{tv}/season/{season}/episode/{episode}?api_key={settings.TMDB_API3_KEY}&language={TMDB_DEFAULT_LANG}&append_to_response=external_ids"
    res_data = BasicDownloader(tmdb_api_url).download().json()
    return res_data


def _copy_dict(s, key_map):
    d = {}
    for src, dst in key_map.items():
        d[dst if dst else src] = s.get(src)
    return d


@SiteManager.register
class TMDB_Movie(AbstractSite):
    SITE_NAME = SiteName.TMDB
    ID_TYPE = IdType.TMDB_Movie
    URL_PATTERNS = [r"^\w+://www.themoviedb.org/movie/(\d+)"]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Movie

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.themoviedb.org/movie/{id_value}"

    def scrape(self):
        res_data = {}
        localized_title = []
        localized_desc = []
        # GET api urls in all locales
        # btw it seems no way to tell if TMDB does not have a certain translation
        for lang, lang_param in reversed(TMDB_PREFERRED_LANGS.items()):
            api_url = f"https://api.themoviedb.org/3/movie/{self.id_value}?api_key={settings.TMDB_API3_KEY}&language={lang_param}&append_to_response=external_ids,credits"
            res_data = BasicDownloader(api_url).download().json()
            if (
                res_data["original_title"] == res_data["title"]
                and res_data["original_language"].split("-")[0]
                != lang_param.split("-")[0]
            ):
                continue
            localized_title.append({"lang": lang, "text": res_data["title"]})
            if res_data.get("overview", "").strip():
                localized_desc.append({"lang": lang, "text": res_data["overview"]})
        title = res_data["title"]
        orig_title = res_data["original_title"]
        orig_lang = res_data["original_language"]
        if orig_title not in [t["text"] for t in localized_title]:
            localized_title.append({"lang": orig_lang, "text": orig_title})
        year = (
            int(res_data["release_date"].split("-")[0])
            if res_data["release_date"]
            else None
        )
        showtime = (
            [{"time": res_data["release_date"], "region": "发布日期"}]
            if res_data["release_date"]
            else None
        )
        imdb_code = res_data["imdb_id"]
        # in minutes
        duration = res_data["runtime"] if res_data["runtime"] else None

        genre = [x["name"] for x in res_data["genres"]]
        language = list(map(lambda x: x["name"], res_data["spoken_languages"]))
        brief = res_data["overview"]

        director = list(
            map(
                lambda x: x["name"],
                filter(lambda c: c["job"] == "Director", res_data["credits"]["crew"]),
            )
        )
        playwright = list(
            map(
                lambda x: x["name"],
                filter(lambda c: c["job"] == "Screenplay", res_data["credits"]["crew"]),
            )
        )
        actor = list(map(lambda x: x["name"], res_data["credits"]["cast"]))
        area = []

        # other_info = {}
        # other_info['TMDB评分'] = res_data['vote_average']
        # other_info['分级'] = res_data['contentRating']
        # other_info['Metacritic评分'] = res_data['metacriticRating']
        # other_info['奖项'] = res_data['awards']
        # other_info['TMDB_ID'] = id
        # if is_series:
        #     other_info["Seasons"] = res_data["number_of_seasons"]
        #     other_info["Episodes"] = res_data["number_of_episodes"]

        # TODO: use GET /configuration to get base url
        img_url = (
            ("https://image.tmdb.org/t/p/original/" + res_data["poster_path"])
            if res_data.get("poster_path") is not None
            else None
        )

        pd = ResourceContent(
            metadata={
                "localized_title": localized_title,
                "localized_description": localized_desc,
                "title": title,
                "orig_title": orig_title,
                "orig_language": orig_lang,
                "other_title": [],
                "imdb_code": imdb_code,
                "director": director,
                "playwright": playwright,
                "actor": actor,
                "genre": genre,
                "showtime": showtime,
                "site": None,
                "area": area,
                "language": language,
                "year": year,
                "duration": duration,
                "season": None,
                "episodes": None,
                "single_episode_length": None,
                "brief": brief,
                "cover_image_url": img_url,
            }
        )
        if imdb_code:
            pd.lookup_ids[IdType.IMDB] = imdb_code
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["movietv", "all", "movie", "tv"]:
            return []
        p = (page - 1) * page_size // 20 + 1
        offset = (page - 1) * page_size % 20
        results = []
        api_url = f"https://api.themoviedb.org/3/search/multi?query={quote_plus(q)}&page={p}&api_key={settings.TMDB_API3_KEY}&language={TMDB_DEFAULT_LANG}&include_adult=true"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(api_url, timeout=2)
                j = response.json()
                if j.get("results"):
                    for m in j["results"]:
                        if m["media_type"] in ["tv", "movie"]:
                            url = f"https://www.themoviedb.org/{m['media_type']}/{m['id']}"
                            if m["media_type"] == "tv":
                                cat = ItemCategory.TV
                                title = m["name"]
                                subtitle = f"{m.get('first_air_date', '')} {m.get('original_name', '')}"
                            else:
                                cat = ItemCategory.Movie
                                title = m["title"]
                                subtitle = f"{m.get('release_date', '')} {m.get('original_name', '')}"
                            cover = (
                                f"https://image.tmdb.org/t/p/w500/{m.get('poster_path')}"
                                if m.get("poster_path")
                                else ""
                            )
                            results.append(
                                ExternalSearchResultItem(
                                    cat,
                                    SiteName.TMDB,
                                    url,
                                    title,
                                    subtitle,
                                    m.get("overview"),
                                    cover,
                                )
                            )
                else:
                    logger.warning(f"TMDB search '{q}' no results found.")
            except httpx.ReadTimeout:
                logger.warning("TMDb search timeout", extra={"query": q})
            except Exception as e:
                logger.error("TMDb search error", extra={"query": q, "exception": e})
        return results[offset : offset + page_size]


@SiteManager.register
class TMDB_TV(AbstractSite):
    SITE_NAME = SiteName.TMDB
    ID_TYPE = IdType.TMDB_TV
    URL_PATTERNS = [
        r"^\w+://www.themoviedb.org/tv/(\d+)[^/]*/?$",
        r"^\w+://www.themoviedb.org/tv/(\d+)[^/]*/seasons$",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = TVShow

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.themoviedb.org/tv/{id_value}"

    def scrape(self):
        res_data = {}
        localized_title = []
        localized_desc = []
        for lang, lang_param in reversed(TMDB_PREFERRED_LANGS.items()):
            api_url = f"https://api.themoviedb.org/3/tv/{self.id_value}?api_key={settings.TMDB_API3_KEY}&language={lang_param}&append_to_response=external_ids,credits"
            res_data = BasicDownloader(api_url).download().json()
            if (
                res_data["original_name"] == res_data["name"]
                and res_data["original_language"].split("-")[0]
                != lang_param.split("-")[0]
            ):
                continue
            localized_title.append({"lang": lang, "text": res_data["name"]})
            if res_data.get("overview", "").strip():
                localized_desc.append({"lang": lang, "text": res_data["overview"]})

        title = res_data["name"]
        orig_title = res_data["original_name"]
        orig_lang = res_data["original_language"]
        if orig_title not in [t["text"] for t in localized_title]:
            localized_title.append({"lang": orig_lang, "text": orig_title})
        year = (
            int(res_data["first_air_date"].split("-")[0])
            if res_data["first_air_date"]
            else None
        )
        imdb_code = res_data["external_ids"]["imdb_id"]
        showtime = (
            [{"time": res_data["first_air_date"], "region": "首播日期"}]
            if res_data["first_air_date"]
            else None
        )
        duration = None
        genre = [x["name"] for x in res_data["genres"]]
        language = list(map(lambda x: x["name"], res_data["spoken_languages"]))
        brief = res_data["overview"]
        director = list(map(lambda x: x["name"], res_data["created_by"]))
        playwright = list(
            map(
                lambda x: x["name"],
                filter(lambda c: c["job"] == "Screenplay", res_data["credits"]["crew"]),
            )
        )
        actor = list(map(lambda x: x["name"], res_data["credits"]["cast"]))
        area = []
        # other_info = {}
        # other_info["Seasons"] = res_data["number_of_seasons"]
        # other_info["Episodes"] = res_data["number_of_episodes"]
        # TODO: use GET /configuration to get base url
        img_url = (
            ("https://image.tmdb.org/t/p/original/" + res_data["poster_path"])
            if res_data.get("poster_path") is not None
            else None
        )
        season_links = list(
            map(
                lambda s: {
                    "model": "TVSeason",
                    "id_type": IdType.TMDB_TVSeason,
                    "id_value": f"{self.id_value}-{s['season_number']}",
                    "title": s["name"],
                    "url": f"{self.url}/season/{s['season_number']}",
                },
                res_data["seasons"],
            )
        )
        pd = ResourceContent(
            metadata={
                "localized_title": localized_title,
                "localized_description": localized_desc,
                "title": title,
                "orig_title": orig_title,
                "orig_language": orig_lang,
                "other_title": [],
                "imdb_code": imdb_code,
                "director": director,
                "playwright": playwright,
                "actor": actor,
                "genre": genre,
                "showtime": showtime,
                "site": None,
                "area": area,
                "language": language,
                "year": year,
                "duration": duration,
                "season_count": res_data["number_of_seasons"],
                "season": None,
                "episodes": None,
                "single_episode_length": None,
                "brief": brief,
                "cover_image_url": img_url,
                "related_resources": season_links,  # FIXME crawling them for now, but many douban tv season data may have wrong imdb links
            }
        )
        if imdb_code:
            pd.lookup_ids[IdType.IMDB] = imdb_code
        return pd


@SiteManager.register
class TMDB_TVSeason(AbstractSite):
    SITE_NAME = SiteName.TMDB
    ID_TYPE = IdType.TMDB_TVSeason
    URL_PATTERNS = [r"^\w+://www.themoviedb.org/tv/(\d+)[^/]*/season/(\d+)[^/]*/?$"]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = TVSeason
    ID_PATTERN = r"^(\d+)-(\d+)$"

    @classmethod
    def url_to_id(cls, url: str):
        u = next(
            iter([re.match(p, url) for p in cls.URL_PATTERNS if re.match(p, url)]), None
        )
        return u[1] + "-" + u[2] if u else None

    @classmethod
    def id_to_url(cls, id_value):
        v = id_value.split("-")
        return f"https://www.themoviedb.org/tv/{v[0]}/season/{v[1]}"

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        v = self.id_value.split("-")
        show_id = v[0]
        season_id = v[1]
        site = TMDB_TV(TMDB_TV.id_to_url(show_id))
        show_resource = site.get_resource_ready(auto_create=False, auto_link=False)
        if not show_resource:
            raise ValueError(f"TMDB: failed to get show for season {self.url}")

        res_data = {}
        localized_title = []
        localized_desc = []
        for lang, lang_param in reversed(TMDB_PREFERRED_LANGS.items()):
            api_url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_id}?api_key={settings.TMDB_API3_KEY}&language={lang_param}&append_to_response=external_ids,credits"
            res_data = BasicDownloader(api_url).download().json()
            localized_title.append({"lang": lang, "text": res_data["name"]})
            if res_data.get("overview", "").strip():
                localized_desc.append({"lang": lang, "text": res_data["overview"]})
        if not res_data.get("id"):
            raise ParseError(self, "id")
        d = res_data
        r = _copy_dict(
            res_data,
            {
                "name": "title",
                "overview": "brief",
                "air_date": "air_date",
                "season_number": 0,
                "external_ids": [],
            },
        )
        r["localized_title"] = localized_title
        r["localized_description"] = localized_desc
        pd = ResourceContent(metadata=r)
        pd.metadata["title"] = (
            show_resource.metadata["title"] + " " + pd.metadata["title"]
        )
        pd.metadata["required_resources"] = [
            {
                "model": "TVShow",
                "id_type": IdType.TMDB_TV,
                "id_value": show_id,
                "title": f"TMDB TV Show {show_id}",
                "url": f"https://www.themoviedb.org/tv/{show_id}",
            }
        ]
        pd.lookup_ids[IdType.IMDB] = d["external_ids"].get("imdb_id")
        pd.metadata["cover_image_url"] = (
            ("https://image.tmdb.org/t/p/original/" + d["poster_path"])
            if d.get("poster_path")
            else None
        )
        pd.metadata["title"] = (
            pd.metadata["title"]
            if pd.metadata.get("title")
            else f"Season {d['season_number']}"
        )
        pd.metadata["episode_number_list"] = list(
            map(lambda ep: ep["episode_number"], d["episodes"])
        )
        pd.metadata["episode_count"] = len(pd.metadata["episode_number_list"])

        # use show's IMDB (for Season 1) or 1st episode's IMDB (if not Season 1) as this season's IMDB so that it can be compatible with TVSeason data from Douban
        if pd.lookup_ids.get(IdType.IMDB):
            # this should not happen
            _logger.warning("Unexpected IMDB id for TMDB tv season")
        elif pd.metadata.get("season_number") == 1:
            site = SiteManager.get_site_by_url(
                f"https://www.themoviedb.org/tv/{show_id}"
            )
            if site:
                res = site.get_resource_ready()
                pd.lookup_ids[IdType.IMDB] = (
                    res.other_lookup_ids.get(IdType.IMDB) if res else None
                )
        elif len(pd.metadata["episode_number_list"]) == 0:
            _logger.warning(
                "Unable to lookup IMDB id for TMDB tv season with zero episodes"
            )
        else:
            ep = pd.metadata["episode_number_list"][0]
            api_url2 = f"https://api.themoviedb.org/3/tv/{v[0]}/season/{v[1]}/episode/{ep}?api_key={settings.TMDB_API3_KEY}&language={TMDB_DEFAULT_LANG}&append_to_response=external_ids,credits"
            d2 = BasicDownloader(api_url2).download().json()
            if not d2.get("id"):
                raise ParseError(self, "first episode id for season")
            pd.lookup_ids[IdType.IMDB] = d2["external_ids"].get("imdb_id")
        return pd


@SiteManager.register
class TMDB_TVEpisode(AbstractSite):
    SITE_NAME = SiteName.TMDB
    ID_TYPE = IdType.TMDB_TVEpisode
    URL_PATTERNS = [
        r"\w+://www.themoviedb.org/tv/(\d+)[^/]*/season/(\d+)/episode/(\d+)[^/]*$"
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = TVEpisode
    ID_PATTERN = r"^(\d+)-(\d+)-(\d+)$"

    @classmethod
    def url_to_id(cls, url: str):
        u = next(
            iter([re.match(p, url) for p in cls.URL_PATTERNS if re.match(p, url)]), None
        )
        return u[1] + "-" + u[2] + "-" + u[3] if u else None

    @classmethod
    def id_to_url(cls, id_value):
        v = id_value.split("-")
        return f"https://www.themoviedb.org/tv/{v[0]}/season/{v[1]}/episode/{v[2]}"

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "id_value")
        v = self.id_value.split("-")
        show_id = v[0]
        season_id = v[1]
        episode_id = v[2]
        site = TMDB_TV(TMDB_TV.id_to_url(show_id))
        site.get_resource_ready(auto_create=False, auto_link=False)
        api_url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_id}/episode/{episode_id}?api_key={settings.TMDB_API3_KEY}&language={TMDB_DEFAULT_LANG}&append_to_response=external_ids,credits"
        d = BasicDownloader(api_url).download().json()
        if not d.get("id"):
            raise ParseError(self, "id")
        pd = ResourceContent(
            metadata=_copy_dict(
                d,
                {
                    "name": "title",
                    "overview": "brief",
                    # "air_date": "air_date",
                    "season_number": 0,
                    "episode_number": 0,
                    "external_ids": [],
                },
            )
        )
        pd.metadata["required_resources"] = [
            {
                "model": "TVSeason",
                "id_type": IdType.TMDB_TVSeason,
                "id_value": f"{show_id}-{season_id}",
                "title": f"TMDB TV Season {show_id}-{season_id}",
                "url": f"https://www.themoviedb.org/tv/{show_id}/season/{season_id}",
            }
        ]
        pd.lookup_ids[IdType.IMDB] = d["external_ids"].get("imdb_id")
        pd.metadata["cover_image_url"] = (
            ("https://image.tmdb.org/t/p/original/" + d["poster_path"])
            if d.get("poster_path")
            else None
        )
        pd.metadata["title"] = (
            pd.metadata["title"]
            if pd.metadata["title"]
            else f"S{d['season_number']} E{d['episode_number']}"
        )

        if pd.lookup_ids.get(IdType.IMDB):
            pd.lookup_ids[IdType.IMDB] = pd.lookup_ids[IdType.IMDB]
        return pd
