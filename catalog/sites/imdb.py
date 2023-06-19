import json
from catalog.common import *
from .tmdb import search_tmdb_by_imdb_id
from catalog.movie.models import *
from catalog.tv.models import *
import logging


_logger = logging.getLogger(__name__)


@SiteManager.register
class IMDB(AbstractSite):
    """
    IMDb site manager

    IMDB ids map to Movie, TVShow or TVEpisode
    IMDB
    """

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
        url = None
        pd = None
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
            url = f"https://www.themoviedb.org/tv/{tv_id}/season/{season_number}/episode/{episode_number}"
        if url:
            tmdb = SiteManager.get_site_by_url(url)
            pd = tmdb.scrape()
            pd.metadata["preferred_model"] = tmdb.DEFAULT_MODEL.__name__
            pd.metadata["required_resources"] = []  # do not auto fetch parent season
        if not pd:
            # if IMDB id not found in TMDB, use real IMDB scraper
            pd = self.scrape_imdb()
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
        if d.get("series"):
            episode_info = d["series"].get("episodeNumber")
            if episode_info:
                data["season_number"] = episode_info["seasonNumber"]
                data["episode_number"] = episode_info["episodeNumber"]
            series = d["series"].get("series")
            if series:
                data["show_imdb_id"] = series["id"]
        # TODO more data fields and localized title (in <url>releaseinfo/)
        data["preferred_model"] = (
            "TVEpisode"
            if data["is_episode"]
            else ("TVShow" if data["is_series"] else "Movie")
        )
        if data["preferred_model"] == "TVEpisode" and data["title"].startswith(
            "Episode #"
        ):
            data["title"] = re.sub(r"#(\d+).(\d+)", r"S\1E\2", data["title"][8:])
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

    @staticmethod
    def get_episode_list(show_id, season_id):
        url = f"https://m.imdb.com/title/{show_id}/"
        h = BasicDownloader(url).download().html()
        show_url = "".join(
            h.xpath('//a[@data-testid="hero-title-block__series-link"]/@href')
        ).split("?")[0]
        if not show_url:
            show_url = f"/title/{show_id}/"
        url = f"https://m.imdb.com{show_url}episodes/?season={season_id}"
        h = BasicDownloader(url).download().html()
        episodes = []
        for e in h.xpath('//div[@id="eplist"]/div/a'):
            episode_number = e.xpath(
                './span[contains(@class,"episode-list__title")]/text()'
            )[0].strip()
            episode_number = int(episode_number.split(".")[0])
            episode_title = " ".join(
                e.xpath('.//strong[@class="episode-list__title-text"]/text()')
            ).strip()
            episode_url = e.xpath("./@href")[0]
            episode_url = "https://www.imdb.com" + episode_url
            episodes.append(
                {
                    "model": "TVEpisode",
                    "id_type": IdType.IMDB,
                    "id_value": IMDB.url_to_id(episode_url),
                    "url": episode_url,
                    "title": episode_title,
                    "episode_number": episode_number,
                }
            )
        return episodes

    @staticmethod
    def fetch_episodes_for_season(season_uuid):
        season = TVSeason.get_by_url(season_uuid)
        if not season.season_number or not season.imdb:
            _logger.warning(f"season {season} is missing season number or imdb id")
            return
        episodes = IMDB.get_episode_list(season.imdb, season.season_number)
        if episodes:
            if not season.episode_count or season.episode_count < len(episodes):
                season.episode_count = len(episodes)
                season.save()
            for e in episodes:
                episode = TVEpisode.objects.filter(
                    season=season, episode_number=e["episode_number"]
                ).first()
                if not episode:
                    site = SiteManager.get_site_by_url(e["url"])
                    episode = site.get_resource_ready().item
                    episode.set_parent_item(season)
                    episode.save()
        else:
            _logger.warning(f"season {season} has no episodes fetched, creating dummy")
            cnt = int(season.episode_count or 0)
            if cnt > 20:
                cnt = 20
            for i in range(1, cnt + 1):
                episode = TVEpisode.objects.filter(
                    season=season, episode_number=i
                ).first()
                if not episode:
                    TVEpisode.objects.create(
                        title=f"S{season.season_number or '0'}E{i}",
                        season=season,
                        episode_number=i,
                    )
