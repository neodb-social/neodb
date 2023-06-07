import json
from catalog.common import *
from .tmdb import search_tmdb_by_imdb_id
from catalog.movie.models import *
from catalog.tv.models import *
import logging


_logger = logging.getLogger(__name__)


@SiteManager.register
class IMDB(AbstractSite):
    SITE_NAME = SiteName.IMDB
    ID_TYPE = IdType.IMDB
    URL_PATTERNS = [
        r"\w+://www.imdb.com/title/(tt\d+)",
        r"\w+://m.imdb.com/title/(tt\d+)",
    ]
    WIKI_PROPERTY_ID = "?"

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.imdb.com/title/" + id_value + "/"

    def scrape(self):
        res_data = search_tmdb_by_imdb_id(self.id_value)
        if (
            "movie_results" in res_data
            and len(res_data["movie_results"]) > 0
            and self.DEFAULT_MODEL in [None, Movie]
        ):
            url = (
                f"https://www.themoviedb.org/movie/{res_data['movie_results'][0]['id']}"
            )
        elif "tv_results" in res_data and len(res_data["tv_results"]) > 0:
            url = f"https://www.themoviedb.org/tv/{res_data['tv_results'][0]['id']}"
        elif "tv_season_results" in res_data and len(res_data["tv_season_results"]) > 0:
            # this should not happen given IMDB only has ids for either show or episode
            tv_id = res_data["tv_season_results"][0]["show_id"]
            season_number = res_data["tv_season_results"][0]["season_number"]
            url = f"https://www.themoviedb.org/tv/{tv_id}/season/{season_number}"
        elif (
            "tv_episode_results" in res_data and len(res_data["tv_episode_results"]) > 0
        ):
            tv_id = res_data["tv_episode_results"][0]["show_id"]
            season_number = res_data["tv_episode_results"][0]["season_number"]
            episode_number = res_data["tv_episode_results"][0]["episode_number"]
            if season_number == 0:
                url = f"https://www.themoviedb.org/tv/{tv_id}/season/{season_number}/episode/{episode_number}"
            elif episode_number == 1:
                url = f"https://www.themoviedb.org/tv/{tv_id}/season/{season_number}"
            else:
                raise ParseError(
                    self,
                    "IMDB id matching TMDB but not first episode, this is not supported",
                )
        else:
            # IMDB id not found in TMDB use real IMDB scraper
            return self.scrape_imdb()
        tmdb = SiteManager.get_site_by_url(url)
        pd = tmdb.scrape()
        pd.metadata["preferred_model"] = tmdb.DEFAULT_MODEL.__name__
        return pd

    def scrape_imdb(self):
        h = BasicDownloader(self.url).download().html()
        elem = h.xpath('//script[@id="__NEXT_DATA__"]/text()')
        src = elem[0].strip() if elem else None
        if not src:
            raise ParseError(self, "__NEXT_DATA__ element")
        d = json.loads(src)["props"]["pageProps"]["aboveTheFoldData"]
        data = {
            "title": d["titleText"]["text"],
            "year": d["releaseYear"]["year"],
            "is_series": d["titleType"]["isSeries"],
            "is_episode": d["titleType"]["isEpisode"],
            "genre": [x["text"] for x in d["genres"]["genres"]],
            "brief": d["plot"].get("plotText") if d.get("plot") else None,
            "cover_image_url": d["primaryImage"].get("url")
            if d.get("primaryImage")
            else None,
        }
        # TODO more data fields and localized title (in <url>releaseinfo/)
        data["preferred_model"] = (
            ""  # "TVSeason" not supported yet
            if data["is_episode"]
            else ("TVShow" if data["is_series"] else "Movie")
        )

        pd = ResourceContent(metadata=data)
        pd.lookup_ids[IdType.IMDB] = self.id_value
        if pd.metadata["cover_image_url"]:
            imgdl = BasicImageDownloader(pd.metadata["cover_image_url"], self.url)
            try:
                pd.cover_image = imgdl.download().content
                pd.cover_image_extention = imgdl.extention
            except Exception:
                _logger.debug(
                    f'failed to download cover for {self.url} from {pd.metadata["cover_image_url"]}'
                )
        return pd
