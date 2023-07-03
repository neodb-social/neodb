from catalog.common import *
from .douban import *
from catalog.movie.models import *
from catalog.tv.models import *
import logging
import json
from django.utils.translation import gettext_lazy as _
from .tmdb import TMDB_TV, TMDB_TVSeason, search_tmdb_by_imdb_id, query_tmdb_tv_episode


_logger = logging.getLogger(__name__)


@SiteManager.register
class DoubanMovie(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanMovie
    URL_PATTERNS = [
        r"\w+://movie\.douban\.com/subject/(\d+)/{0,1}",
        r"\w+://m.douban.com/movie/subject/(\d+)/{0,1}",
    ]
    WIKI_PROPERTY_ID = "?"
    # no DEFAULT_MODEL as it may be either TV Season and Movie

    @classmethod
    def id_to_url(cls, id_value):
        return "https://movie.douban.com/subject/" + id_value + "/"

    def scrape(self):
        content = DoubanDownloader(self.url).download().html()
        try:
            schema_data = "".join(
                content.xpath('//script[@type="application/ld+json"]/text()')
            ).replace(
                "\n", ""
            )  # strip \n bc multi-line string is not properly coded in json by douban
            d = json.loads(schema_data) if schema_data else {}
        except Exception as e:
            d = {}

        try:
            raw_title = content.xpath("//span[@property='v:itemreviewed']/text()")[
                0
            ].strip()
        except IndexError:
            raise ParseError(self, "title")

        orig_title = content.xpath("//img[@rel='v:image']/@alt")[0].strip()
        title = raw_title.split(orig_title)[0].strip()
        # if has no chinese title
        if title == "":
            title = orig_title

        if title == orig_title:
            orig_title = None

        # there are two html formats for authors and translators
        other_title_elem = content.xpath(
            "//div[@id='info']//span[text()='又名:']/following-sibling::text()[1]"
        )
        other_title = (
            other_title_elem[0].strip().split(" / ") if other_title_elem else None
        )

        imdb_elem = content.xpath(
            "//div[@id='info']//span[text()='IMDb链接:']/following-sibling::a[1]/text()"
        )
        if not imdb_elem:
            imdb_elem = content.xpath(
                "//div[@id='info']//span[text()='IMDb:']/following-sibling::text()[1]"
            )
        imdb_code = imdb_elem[0].strip() if imdb_elem else None

        director_elem = content.xpath(
            "//div[@id='info']//span[text()='导演']/following-sibling::span[1]/a/text()"
        )
        director = director_elem if director_elem else None

        playwright_elem = content.xpath(
            "//div[@id='info']//span[text()='编剧']/following-sibling::span[1]/a/text()"
        )
        playwright = (
            list(map(lambda a: a[:200], playwright_elem)) if playwright_elem else None
        )

        actor_elem = content.xpath(
            "//div[@id='info']//span[text()='主演']/following-sibling::span[1]/a/text()"
        )
        actor = list(map(lambda a: a[:200], actor_elem)) if actor_elem else None

        genre_elem = content.xpath("//span[@property='v:genre']/text()")
        genre = []
        if genre_elem:
            for g in genre_elem:
                g = g.split(" ")[0]
                if g == "紀錄片":  # likely some original data on douban was corrupted
                    g = "纪录片"
                elif g == "鬼怪":
                    g = "惊悚"
                genre.append(g)

        showtime_elem = content.xpath("//span[@property='v:initialReleaseDate']/text()")
        if showtime_elem:
            showtime = []
            for st in showtime_elem:
                parts = st.split("(")
                if len(parts) == 1:
                    time = st.split("(")[0]
                    region = ""
                else:
                    time = st.split("(")[0]
                    region = st.split("(")[1][0:-1]
                showtime.append(
                    {
                        "region": region,
                        "time": time,
                    }
                )
        else:
            showtime = None

        site_elem = content.xpath(
            "//div[@id='info']//span[text()='官方网站:']/following-sibling::a[1]/@href"
        )
        site = site_elem[0].strip()[:200] if site_elem else None
        if site and not re.match(r"http.+", site):
            site = None

        area_elem = content.xpath(
            "//div[@id='info']//span[text()='制片国家/地区:']/following-sibling::text()[1]"
        )
        if area_elem:
            area = [a.strip()[:100] for a in area_elem[0].split("/")]
        else:
            area = None

        language_elem = content.xpath(
            "//div[@id='info']//span[text()='语言:']/following-sibling::text()[1]"
        )
        if language_elem:
            language = [a.strip() for a in language_elem[0].split(" / ")]
        else:
            language = None

        year_elem = content.xpath("//span[@class='year']/text()")
        year = (
            int(re.search(r"\d+", year_elem[0])[0])
            if year_elem and re.search(r"\d+", year_elem[0])
            else None
        )

        duration_elem = content.xpath("//span[@property='v:runtime']/text()")
        other_duration_elem = content.xpath(
            "//span[@property='v:runtime']/following-sibling::text()[1]"
        )
        if duration_elem:
            duration = duration_elem[0].strip()
            if other_duration_elem:
                duration += other_duration_elem[0].rstrip()
            duration = duration.split("/")[0].strip()
        else:
            duration = None

        season_elem = content.xpath(
            "//*[@id='season']/option[@selected='selected']/text()"
        )
        if not season_elem:
            season_elem = content.xpath(
                "//div[@id='info']//span[text()='季数:']/following-sibling::text()[1]"
            )
            season = int(season_elem[0].strip()) if season_elem else None
        else:
            season = int(season_elem[0].strip())

        episodes_elem = content.xpath(
            "//div[@id='info']//span[text()='集数:']/following-sibling::text()[1]"
        )
        episodes = (
            int(episodes_elem[0].strip())
            if episodes_elem and episodes_elem[0].strip().isdigit()
            else None
        )

        single_episode_length_elem = content.xpath(
            "//div[@id='info']//span[text()='单集片长:']/following-sibling::text()[1]"
        )
        single_episode_length = (
            single_episode_length_elem[0].strip()[:100]
            if single_episode_length_elem
            else None
        )

        is_series = d.get("@type") == "TVSeries" or episodes is not None

        brief_elem = content.xpath("//span[@class='all hidden']")
        if not brief_elem:
            brief_elem = content.xpath("//span[@property='v:summary']")
        brief = (
            "\n".join([e.strip() for e in brief_elem[0].xpath("./text()")])
            if brief_elem
            else None
        )

        img_url_elem = content.xpath("//img[@rel='v:image']/@src")
        img_url = img_url_elem[0].strip() if img_url_elem else None

        pd = ResourceContent(
            metadata={
                "title": title,
                "orig_title": orig_title,
                "other_title": other_title,
                "imdb_code": imdb_code,
                "director": director,
                "playwright": playwright,
                "actor": actor,
                "genre": genre,
                "showtime": showtime,
                "site": site,
                "area": area,
                "language": language,
                "year": year,
                "duration": duration,
                "season_number": season,
                "episode_count": episodes,
                "single_episode_length": single_episode_length,
                "brief": brief,
                "is_series": is_series,
                "cover_image_url": img_url,
            }
        )
        pd.metadata["preferred_model"] = (
            "TVSeason" if is_series or episodes or season else "Movie"
        )

        tmdb_season_id = None
        if imdb_code:
            res_data = search_tmdb_by_imdb_id(imdb_code)
            has_movie = (
                "movie_results" in res_data and len(res_data["movie_results"]) > 0
            )
            has_tv = "tv_results" in res_data and len(res_data["tv_results"]) > 0
            has_episode = (
                "tv_episode_results" in res_data
                and len(res_data["tv_episode_results"]) > 0
            )
            if pd.metadata["preferred_model"] == "TVSeason" and has_tv:
                if (
                    pd.metadata.get("season_number")
                    and pd.metadata.get("season_number") != 1
                ):
                    _logger.warn(f"{imdb_code} matched imdb tv show, force season 1")
                    pd.metadata["season_number"] = 1
            elif pd.metadata["preferred_model"] == "TVSeason" and has_episode:
                if res_data["tv_episode_results"][0]["episode_number"] != 1:
                    _logger.warning(
                        f"Douban Movie {self.url} IMDB {imdb_code} mapping to non-first episode in a season"
                    )
                elif res_data["tv_episode_results"][0]["season_number"] == 1:
                    _logger.warning(
                        f"Douban Movie {self.url} IMDB {imdb_code} mapping to first season episode in a season"
                    )
            elif has_movie:
                if pd.metadata["preferred_model"] != "Movie":
                    _logger.warn(f"{imdb_code} matched imdb movie, force Movie")
                    pd.metadata["preferred_model"] = "Movie"
            elif has_tv or has_episode:
                _logger.warn(f"{imdb_code} matched imdb tv/episode, force TVSeason")
                pd.metadata["preferred_model"] = "TVSeason"
            else:
                _logger.warn(f"{imdb_code} unknown to TMDB")

            pd.lookup_ids[IdType.IMDB] = imdb_code

            if pd.metadata["preferred_model"] == "TVSeason":
                tmdb_show_id = None
                if has_tv:
                    tmdb_show_id = res_data["tv_results"][0]["id"]
                elif has_episode:
                    tmdb_show_id = res_data["tv_episode_results"][0]["show_id"]
                if tmdb_show_id:
                    pd.metadata["required_resources"] = [
                        {
                            "model": "TVShow",
                            "id_type": IdType.TMDB_TV,
                            "id_value": tmdb_show_id,
                            "title": title,
                            "url": TMDB_TV.id_to_url(tmdb_show_id),
                        }
                    ]
        # TODO parse sister seasons
        # pd.metadata['related_resources'] = []
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
