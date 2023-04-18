from django.test import TestCase
from catalog.common import *
from catalog.tv.models import *


class JSONFieldTestCase(TestCase):
    def test_legacy_data(self):
        o = TVShow()
        self.assertEqual(o.other_title, [])
        o.other_title = "test"
        self.assertEqual(o.other_title, ["test"])
        o.other_title = ["a", "b"]
        self.assertEqual(o.other_title, ["a", "b"])
        o.other_title = None
        self.assertEqual(o.other_title, None)


class TMDBTVTestCase(TestCase):
    def test_parse(self):
        t_id = "57243"
        t_url = "https://www.themoviedb.org/tv/57243-doctor-who"
        t_url1 = "https://www.themoviedb.org/tv/57243-doctor-who/seasons"
        t_url2 = "https://www.themoviedb.org/tv/57243"
        p1 = SiteManager.get_site_by_id_type(IdType.TMDB_TV)
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
        p1 = SiteManager.get_site_by_id_type(IdType.TMDB_TVSeason)
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
        url2 = "https://www.imdb.com/title/tt1159991/"
        url3 = "https://movie.douban.com/subject/3627919/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.imdb, p2.item.imdb)
        self.assertEqual(p2.item.imdb, p3.item.imdb)
        self.assertEqual(p1.item.id, p2.item.id)
        self.assertEqual(p2.item.id, p3.item.id)

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
