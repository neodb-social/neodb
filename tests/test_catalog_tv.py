import pytest

from catalog.common import *
from catalog.sites.imdb import IMDB
from catalog.tv.models import *


@pytest.mark.django_db(databases="__all__")
class TestTMDBTV:
    def test_parse(self):
        t_id = "57243"
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who"
        t_url1 = "https://www.themoviedb.org/tv/57243-doctor-who/seasons"
        t_url2 = "https://www.themoviedb.org/tv/57243"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_TV)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.validate_url(t_url1)
        assert p1.validate_url(t_url2)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p2 is not None
        assert p1.id_to_url(t_id) == t_url2
        assert p2.url_to_id(t_url) == t_id
        wrong_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/13"
        s1 = SiteManager.get_site_by_url(wrong_url)
        assert s1 is not None
        assert not isinstance(s1, TVShow)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "57243"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Doctor Who"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVShow"
        assert site.resource.item.imdb == "tt0436992"


@pytest.mark.django_db(databases="__all__")
class TestTMDBTVSeason:
    def test_parse(self):
        t_id = "57243-11"
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/11"
        t_url_unique = "https://www.themoviedb.org/tv/57243/season/11"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_TVSeason)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.validate_url(t_url_unique)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p2 is not None
        assert p1.id_to_url(t_id) == t_url_unique
        assert p2.url_to_id(t_url) == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/4"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "57243-4"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Doctor Who Series 4"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVSeason"
        assert site.resource.item.imdb == "tt1159991"
        assert site.resource.item.show is not None
        assert site.resource.item.show.imdb == "tt0436992"


@pytest.mark.django_db(databases="__all__")
class TestTMDBEpisode:
    @use_local_response
    def test_scrape_tmdb(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/4/episode/1"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "57243-4-1"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Partners in Crime"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVEpisode"
        assert site.resource.item.imdb == "tt1159991"
        assert site.resource.item.season is not None
        assert site.resource.item.season.imdb == "tt1159991"
        # assert site.resource.item.season.show is not None
        # assert site.resource.item.season.show.imdb == "tt0436992"


@pytest.mark.django_db(databases="__all__")
class TestDoubanMovieTV:
    @use_local_response
    def test_scrape(self):
        url3 = "https://movie.douban.com/subject/3627919/"
        site3 = SiteManager.get_site_by_url(url3)
        assert site3 is not None
        p3 = site3.get_resource_ready()
        assert p3 is not None
        assert p3.item is not None
        assert p3.item.__class__.__name__ == "TVSeason"
        assert p3.item.show is not None
        assert p3.item.show.imdb == "tt0436992"

    @use_local_response
    def test_scrape_singleseason(self):
        url3 = "https://movie.douban.com/subject/26895436/"
        site3 = SiteManager.get_site_by_url(url3)
        assert site3 is not None
        p3 = site3.get_resource_ready()
        assert p3 is not None
        assert p3.item is not None
        assert p3.item.__class__.__name__ == "TVSeason"

    @use_local_response
    def test_scrape_fix_imdb(self):
        # this douban links to S6E3, we'll change it to S6E1 to keep consistant
        url = "https://movie.douban.com/subject/35597581/"
        site = SiteManager.get_site_by_url(url)
        assert site is not None
        resource = site.get_resource_ready()
        assert resource is not None
        assert resource.item is not None
        item = resource.item
        # disable this test to make douban data less disrupted
        assert item.imdb == "tt21599650"


@pytest.mark.django_db(databases="__all__")
class TestMultiTVSites:
    @use_local_response
    def test_tvshows(self):
        url1 = "https://www.themoviedb.org/tv/57243-doctor-who"
        url2 = "https://www.imdb.com/title/tt0436992/"
        # url3 = 'https://movie.douban.com/subject/3541415/'
        site1 = SiteManager.get_site_by_url(url1)
        site2 = SiteManager.get_site_by_url(url2)
        assert site1 is not None
        assert site2 is not None
        p1 = site1.get_resource_ready()
        p2 = site2.get_resource_ready()
        # p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        assert p2 is not None
        assert p2.item is not None
        assert p1.item.id == p2.item.id
        # assert p2.item.id == p3.item.id

    @use_local_response
    def test_tvseasons(self):
        url1 = "https://www.themoviedb.org/tv/57243-doctor-who/season/4"
        url2 = "https://movie.douban.com/subject/3627919/"
        url3 = "https://www.imdb.com/title/tt1159991/"
        site1 = SiteManager.get_site_by_url(url1)
        site2 = SiteManager.get_site_by_url(url2)
        site3 = SiteManager.get_site_by_url(url3)
        assert site1 is not None
        assert site2 is not None
        assert site3 is not None
        p1 = site1.get_resource_ready()
        p2 = site2.get_resource_ready()
        p3 = site3.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        assert p2 is not None
        assert p2.item is not None
        assert p3 is not None
        assert p3.item is not None
        assert p1.item.imdb == p2.item.imdb
        assert p2.item.imdb == p3.item.imdb
        assert p1.item.id == p2.item.id
        assert p2.item.id != p3.item.id

    @use_local_response
    def test_miniseries(self):
        url1 = "https://www.themoviedb.org/tv/86941-the-north-water"
        url3 = "https://movie.douban.com/subject/26895436/"
        site1 = SiteManager.get_site_by_url(url1)
        site3 = SiteManager.get_site_by_url(url3)
        assert site1 is not None
        assert site3 is not None
        p1 = site1.get_resource_ready()
        p3 = site3.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        assert p3 is not None
        assert p3.item is not None
        assert p3.item.__class__.__name__ == "TVSeason"
        assert p3.item.show is not None
        assert p1.item == p3.item.show

    @use_local_response
    def test_tvspecial(self):
        url1 = "https://www.themoviedb.org/movie/282758-doctor-who-the-runaway-bride"
        url2 = "https://www.imdb.com/title/tt0827573/"
        url3 = "https://movie.douban.com/subject/4296866/"
        site1 = SiteManager.get_site_by_url(url1)
        site2 = SiteManager.get_site_by_url(url2)
        site3 = SiteManager.get_site_by_url(url3)
        assert site1 is not None
        assert site2 is not None
        assert site3 is not None
        p1 = site1.get_resource_ready()
        p2 = site2.get_resource_ready()
        p3 = site3.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        assert p2 is not None
        assert p2.item is not None
        assert p3 is not None
        assert p3.item is not None
        assert p1.item.imdb == p2.item.imdb
        assert p2.item.imdb == p3.item.imdb
        assert p1.item.id == p2.item.id
        assert p2.item.id == p3.item.id


@pytest.mark.django_db(databases="__all__")
class TestMovieTVModelRecast:
    @use_local_response
    def test_recast(self):
        from catalog.models import Movie

        url2 = "https://www.imdb.com/title/tt0436992/"
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert p2.item is not None
        tv = p2.item
        assert tv.class_name == "tvshow"
        assert tv.display_title == "Doctor Who"
        movie = tv.recast_to(Movie)
        assert movie.class_name == "movie"
        assert movie.display_title == "Doctor Who"


@pytest.mark.django_db(databases="__all__")
class TestIMDB:
    @use_local_response
    def test_fetch_episodes(self):
        t_url = "https://movie.douban.com/subject/1920763/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        resource = site.get_resource_ready()
        assert resource is not None
        assert resource.item is not None
        season = resource.item
        assert season is not None
        assert season.season_number is None
        IMDB.fetch_episodes_for_season(season)
        # no episodes fetch bc no season number
        episodes = list(season.episodes.all().order_by("episode_number"))
        assert len(episodes) == 0
        # set season number and fetch again
        season.season_number = 1
        season.save()
        IMDB.fetch_episodes_for_season(season)
        episodes = list(season.episodes.all().order_by("episode_number"))
        assert len(episodes) == 2
        # fetch again, no duplicated episodes
        IMDB.fetch_episodes_for_season(season)
        episodes2 = list(season.episodes.all().order_by("episode_number"))
        assert episodes == episodes2
        # delete one episode and fetch again
        episodes[0].delete()
        episodes3 = list(season.episodes.all().order_by("episode_number"))
        assert len(episodes3) == 1
        IMDB.fetch_episodes_for_season(season)
        episodes4 = list(season.episodes.all().order_by("episode_number"))
        assert len(episodes4) == 2
        assert episodes[1] == episodes4[1]

    @use_local_response
    def test_get_episode_list(self):
        episodes = IMDB.get_episode_list("tt0436992", 4)
        assert len(episodes) == 14
        episodes = IMDB.get_episode_list("tt1205438", 4)
        assert len(episodes) == 14

    @use_local_response
    def test_tvshow(self):
        t_url = "https://m.imdb.com/title/tt10751754/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "tt10751754"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Li Shi Na Xie Shi"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVShow"
        assert site.resource.item.year == 2018
        assert site.resource.item.imdb == "tt10751754"

    @use_local_response
    def test_tvepisode_from_tmdb(self):
        t_url = "https://m.imdb.com/title/tt1159991/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "tt1159991"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Partners in Crime"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVEpisode"
        assert site.resource.item.imdb == "tt1159991"
        assert site.resource.item.season_number == 4
        assert site.resource.item.episode_number == 1
        assert site.resource.item.season is None
        # assert site.resource.item.season.imdb == "tt1159991"
        # assert site.resource.item.season.show is not None
        # assert site.resource.item.season.show.imdb == "tt0436992"

    @use_local_response
    def test_tvepisode_from_imdb(self):
        t_url = "https://m.imdb.com/title/tt10751820/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "tt10751820"
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Cong tou kai shi"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "TVEpisode"
        assert site.resource.item.imdb == "tt10751820"
        assert site.resource.item.season_number == 2
        assert site.resource.item.episode_number == 1


@pytest.mark.django_db(databases="__all__")
class TestBangumiTV:
    @use_local_response
    def test_scrape(self):
        url1 = "https://bgm.tv/subject/7157"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        p1 = site1.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        assert p1.item.__class__.__name__ == "TVSeason"
        assert p1.item.orig_title == "ヨスガノソラ"
        assert p1.item.site == "http://king-cr.jp/special/yosuganosora/"
        assert p1.item.director == ["高橋丈夫"]

        url2 = "https://bgm.tv/subject/253"
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert p2.item is not None
        assert p2.item.__class__.__name__ == "TVSeason"
        assert p2.item.orig_title == "カウボーイビバップ"
        assert p2.item.site == "http://www.cowboybebop.org/"
        assert p2.item.director == ["渡辺信一郎"]
        assert p2.item.episode_count == 26
