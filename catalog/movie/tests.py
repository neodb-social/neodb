from django.test import TestCase

from catalog.common import *


class DoubanMovieTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id = "3541415"
        t_url = "https://movie.douban.com/subject/3541415/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.DoubanMovie)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url)
        self.assertEqual(p2.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://movie.douban.com/subject/3541415/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "3541415")
        site.get_resource_ready()
        self.assertEqual(site.resource.metadata["title"], "盗梦空间")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "Movie")
        self.assertEqual(site.resource.item.imdb, "tt1375666")


class TMDBMovieTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id = "293767"
        t_url = (
            "https://www.themoviedb.org/movie/293767-billy-lynn-s-long-halftime-walk"
        )
        t_url2 = "https://www.themoviedb.org/movie/293767"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_Movie)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.validate_url(t_url2), True)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url2)
        self.assertEqual(p2.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/movie/293767"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "293767")
        site.get_resource_ready()
        self.assertEqual(
            site.resource.metadata["title"], "Billy Lynn's Long Halftime Walk"
        )
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.__class__.__name__, "Movie")
        self.assertEqual(site.resource.item.imdb, "tt2513074")


class IMDBMovieTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id = "tt1375666"
        t_url = "https://www.imdb.com/title/tt1375666/"
        t_url2 = "https://www.imdb.com/title/tt1375666/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.IMDB)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.validate_url(t_url2), True)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url2)
        self.assertEqual(p2.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.imdb.com/title/tt1375666/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.id_value, "tt1375666")
        site.get_resource_ready()
        self.assertEqual(site.resource.metadata["title"], "Inception")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.imdb, "tt1375666")


class BangumiMovieTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_scrape(self):
        url = "https://bgm.tv/subject/237"
        site = SiteManager.get_site_by_url(url)
        self.assertEqual(site.id_value, "237")
        site.get_resource_ready()
        self.assertEqual(site.resource.item.display_title, "GHOST IN THE SHELL")
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IMDB)
        self.assertEqual(site.resource.item.imdb, "tt0113568")


class MultiMovieSitesTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_movies(self):
        url1 = "https://www.themoviedb.org/movie/27205-inception"
        url2 = "https://movie.douban.com/subject/3541415/"
        url3 = "https://www.imdb.com/title/tt1375666/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)
        self.assertEqual(p2.item.id, p3.item.id)
