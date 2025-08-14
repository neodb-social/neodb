import pytest

from catalog.common import *


@pytest.mark.django_db(databases="__all__")
class TestDoubanMovie:
    def test_parse(self):
        t_id = "3541415"
        t_url = "https://movie.douban.com/subject/3541415/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.DoubanMovie)
        assert p1 is not None
        assert p1.validate_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://movie.douban.com/subject/3541415/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "3541415"
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.metadata["title"] == "盗梦空间"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "Movie"
        assert site.resource.item.imdb == "tt1375666"


@pytest.mark.django_db(databases="__all__")
class TestTMDBMovie:
    def test_parse(self):
        t_id = "293767"
        t_url = (
            "https://www.themoviedb.org/movie/293767-billy-lynn-s-long-halftime-walk"
        )
        t_url2 = "https://www.themoviedb.org/movie/293767"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.TMDB_Movie)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.validate_url(t_url2)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url2
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/movie/293767"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "293767"
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Billy Lynn's Long Halftime Walk"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.__class__.__name__ == "Movie"
        assert site.resource.item.imdb == "tt2513074"


@pytest.mark.django_db(databases="__all__")
class TestIMDBMovie:
    def test_parse(self):
        t_id = "tt1375666"
        t_url = "https://www.imdb.com/title/tt1375666/"
        t_url2 = "https://www.imdb.com/title/tt1375666/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.IMDB)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.validate_url(t_url2)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url2
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.imdb.com/title/tt1375666/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        assert site.id_value == "tt1375666"
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Inception"
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.imdb == "tt1375666"


@pytest.mark.django_db(databases="__all__")
class TestBangumiMovie:
    @use_local_response
    def test_scrape(self):
        url = "https://bgm.tv/subject/237"
        site = SiteManager.get_site_by_url(url)
        assert site is not None
        assert site.id_value == "237"
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.item is not None
        assert site.resource.item.display_title == "GHOST IN THE SHELL"
        assert site.resource.item.primary_lookup_id_type == IdType.IMDB
        assert site.resource.item.imdb == "tt0113568"


@pytest.mark.django_db(databases="__all__")
class TestMultiMovieSites:
    @use_local_response
    def test_movies(self):
        url1 = "https://www.themoviedb.org/movie/27205-inception"
        url2 = "https://movie.douban.com/subject/3541415/"
        url3 = "https://www.imdb.com/title/tt1375666/"
        url4 = "https://www.wikidata.org/wiki/Q25188"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        p1 = site1.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert p2.item is not None
        site3 = SiteManager.get_site_by_url(url3)
        assert site3 is not None
        p3 = site3.get_resource_ready()
        assert p3 is not None
        assert p3.item is not None
        assert p1.item.id == p2.item.id
        assert p2.item.id == p3.item.id
        site4 = SiteManager.get_site_by_url(url4)
        assert site4 is not None
        p4 = site4.get_resource_ready()
        assert p4 is not None
        assert p4.item == p3.item
