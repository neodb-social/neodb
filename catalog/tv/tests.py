from django.test import TestCase
from catalog.common import *
from catalog.tv.models import *
from catalog.sites.imdb import IMDB


class JSONFieldTestCase(TestCase):
    def test_legacy_data(self):
        o = TVShow()
        self.assertEqual(o.other_title, [])
        o.other_title = "test"
        self.assertEqual(o.other_title, ["test"])
        o.other_title = ["a", "b"]
        self.assertEqual(o.other_title, ["a", "b"])
        o.other_title = None
        self.assertEqual(o.other_title, [])


class TMDBTVTestCase(TestCase):
    def test_parse(self):
        t_id = "57243"
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who"
        t_url1 = "https://www.themoviedb.org/tv/57243-doctor-who/seasons"
        t_url2 = "https://www.themoviedb.org/tv/57243"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_TV)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.validate_url(t_url1), True)
        self.assertEqual(p1.validate_url(t_url2), True)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url2)
        self.assertEqual(p2.url_to_id(t_url), t_id)
        wrong_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/13"
        s1 = SiteManager.get_site_by_url(wrong_url)
        self.assertNotIsInstance(s1, TVShow)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "57243")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "神秘博士")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVShow")
        self.assertEqual(site.resource.item.imdb, "tt0436992")


class TMDBTVSeasonTestCase(TestCase):
    def test_parse(self):
        t_id = "57243-11"
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/11"
        t_url_unique = "https://www.themoviedb.org/tv/57243/season/11"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_TVSeason)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.validate_url(t_url_unique), True)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url_unique)
        self.assertEqual(p2.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/4"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "57243-4")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "神秘博士 第 4 季")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVSeason")
        self.assertEqual(site.resource.item.imdb, "tt1159991")
        self.assertIsNotNone(site.resource.item.show)
        self.assertEqual(site.resource.item.show.imdb, "tt0436992")


class TMDBEpisodeTestCase(TestCase):
    @use_local_response
    def test_scrape_tmdb(self):
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who/season/4/episode/1"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "57243-4-1")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "活宝搭档")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVEpisode")
        self.assertEqual(site.resource.item.imdb, "tt1159991")
        self.assertIsNotNone(site.resource.item.season)
        self.assertEqual(site.resource.item.season.imdb, "tt1159991")
        # self.assertIsNotNone(site.resource.item.season.show)
        # self.assertEqual(site.resource.item.season.show.imdb, "tt0436992")


class DoubanMovieTVTestCase(TestCase):
    @use_local_response
    def test_scrape(self):
        url3 = "https://movie.douban.com/subject/3627919/"
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p3.item.__class__.__name__, "TVSeason")
        self.assertIsNotNone(p3.item.show)
        self.assertEqual(p3.item.show.imdb, "tt0436992")

    @use_local_response
    def test_scrape_singleseason(self):
        url3 = "https://movie.douban.com/subject/26895436/"
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p3.item.__class__.__name__, "TVSeason")

    @use_local_response
    def test_scrape_fix_imdb(self):
        # this douban links to S6E3, we'll change it to S6E1 to keep consistant
        url = "https://movie.douban.com/subject/35597581/"
        item = SiteManager.get_site_by_url(url).get_resource_ready().item
        # disable this test to make douban data less disrupted
        # self.assertEqual(item.imdb, "tt21599650")


class MultiTVSitesTestCase(TestCase):
    @use_local_response
    def test_tvshows(self):
        url1 = "https://www.themoviedb.org/tv/57243-doctor-who"
        url2 = "https://www.imdb.com/title/tt0436992/"
        # url3 = 'https://movie.douban.com/subject/3541415/'
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        # p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)
        # self.assertEqual(p2.item.id, p3.item.id)

    @use_local_response
    def test_tvseasons(self):
        url1 = "https://www.themoviedb.org/tv/57243-doctor-who/season/4"
        url2 = "https://movie.douban.com/subject/3627919/"
        url3 = "https://www.imdb.com/title/tt1159991/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.imdb, p2.item.imdb)
        self.assertEqual(p2.item.imdb, p3.item.imdb)
        self.assertEqual(p1.item.id, p2.item.id)
        self.assertNotEqual(p2.item.id, p3.item.id)

    @use_local_response
    def test_miniseries(self):
        url1 = "https://www.themoviedb.org/tv/86941-the-north-water"
        url3 = "https://movie.douban.com/subject/26895436/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p3.item.__class__.__name__, "TVSeason")
        self.assertEqual(p1.item, p3.item.show)

    @use_local_response
    def test_tvspecial(self):
        url1 = "https://www.themoviedb.org/movie/282758-doctor-who-the-runaway-bride"
        url2 = "hhttps://www.imdb.com/title/tt0827573/"
        url3 = "https://movie.douban.com/subject/4296866/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.imdb, p2.item.imdb)
        self.assertEqual(p2.item.imdb, p3.item.imdb)
        self.assertEqual(p1.item.id, p2.item.id)
        self.assertEqual(p2.item.id, p3.item.id)


class MovieTVModelRecastTestCase(TestCase):
    @use_local_response
    def test_recast(self):
        from catalog.models import Movie, TVShow

        url2 = "https://www.imdb.com/title/tt0436992/"
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        tv = p2.item
        self.assertEqual(tv.class_name, "tvshow")
        self.assertEqual(tv.title, "神秘博士")
        movie = tv.recast_to(Movie)
        self.assertEqual(movie.class_name, "movie")
        self.assertEqual(movie.title, "神秘博士")


class IMDBTestCase(TestCase):
    @use_local_response
    def test_fetch_episodes(self):
        t_url = "https://movie.douban.com/subject/1920763/"
        season = SiteManager.get_site_by_url(t_url).get_resource_ready().item
        self.assertIsNotNone(season)
        self.assertIsNone(season.season_number)
        IMDB.fetch_episodes_for_season(season)
        # no episodes fetch bc no season number
        episodes = list(season.episodes.all().order_by("episode_number"))
        self.assertEqual(len(episodes), 0)
        # set season number and fetch again
        season.season_number = 1
        season.save()
        IMDB.fetch_episodes_for_season(season)
        episodes = list(season.episodes.all().order_by("episode_number"))
        self.assertEqual(len(episodes), 2)
        # fetch again, no duplicated episodes
        IMDB.fetch_episodes_for_season(season)
        episodes2 = list(season.episodes.all().order_by("episode_number"))
        self.assertEqual(episodes, episodes2)
        # delete one episode and fetch again
        episodes[0].delete()
        episodes3 = list(season.episodes.all().order_by("episode_number"))
        self.assertEqual(len(episodes3), 1)
        IMDB.fetch_episodes_for_season(season)
        episodes4 = list(season.episodes.all().order_by("episode_number"))
        self.assertEqual(len(episodes4), 2)
        self.assertEqual(episodes[1], episodes4[1])

    @use_local_response
    def test_get_episode_list(self):
        l = IMDB.get_episode_list("tt0436992", 4)
        self.assertEqual(len(l), 14)
        l = IMDB.get_episode_list("tt1205438", 4)
        self.assertEqual(len(l), 14)

    @use_local_response
    def test_tvshow(self):
        t_url = "https://m.imdb.com/title/tt10751754/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "tt10751754")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Li Shi Na Xie Shi")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVShow")
        self.assertEqual(site.resource.item.year, 2018)
        self.assertEqual(site.resource.item.imdb, "tt10751754")

    @use_local_response
    def test_tvepisode_from_tmdb(self):
        t_url = "https://m.imdb.com/title/tt1159991/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "tt1159991")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "活宝搭档")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVEpisode")
        self.assertEqual(site.resource.item.imdb, "tt1159991")
        self.assertEqual(site.resource.item.season_number, 4)
        self.assertEqual(site.resource.item.episode_number, 1)
        self.assertIsNone(site.resource.item.season)
        # self.assertEqual(site.resource.item.season.imdb, "tt1159991")
        # self.assertIsNotNone(site.resource.item.season.show)
        # self.assertEqual(site.resource.item.season.show.imdb, "tt0436992")

    @use_local_response
    def test_tvepisode_from_imdb(self):
        t_url = "https://m.imdb.com/title/tt10751820/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "tt10751820")
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Cong tou kai shi")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "TVEpisode")
        self.assertEqual(site.resource.item.imdb, "tt10751820")
        self.assertEqual(site.resource.item.season_number, 2)
        self.assertEqual(site.resource.item.episode_number, 1)
