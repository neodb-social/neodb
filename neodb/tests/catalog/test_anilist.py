import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, IdType, Movie, TVSeason
from catalog.sites.anilist import AniListAnime, AniListManga, _base_role


class TestBaseRole:
    def test_strips_episode_scope(self):
        assert _base_role("Director (eps 1-479)") == "director"
        assert _base_role("Series Composition (eps 1-289, 296-479)") == (
            "series composition"
        )
        assert _base_role("Story & Art") == "story & art"

    def test_keeps_qualified_roles_distinct(self):
        # these must not collapse onto "director"
        assert _base_role("Episode Director (ep 480)") == "episode director"
        assert _base_role("ADR Director (Italian; eps 287-348)") == "adr director"
        assert _base_role("Assistant Director") == "assistant director"


@pytest.mark.django_db(databases="__all__")
class TestAniListAnime:
    def test_parse(self):
        t_url = "https://anilist.co/anime/1735/NARUTO-Shippuuden/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.AniList_Anime)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.validate_url("https://anilist.co/anime/1735")
        # manga URLs belong to the other site class
        assert not p1.validate_url("https://anilist.co/manga/30011")
        p2 = SiteManager.get_site_by_url(t_url)
        assert p2 is not None
        assert isinstance(p2, AniListAnime)
        assert p2.id_value == "1735"
        assert p1.id_to_url("1735") == "https://anilist.co/anime/1735"

    @use_local_response
    def test_scrape_tv(self):
        site = SiteManager.get_site_by_url("https://anilist.co/anime/1735")
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "TVSeason"
        assert m["episode_count"] == 500
        assert m["single_episode_length"] == 23 * 60  # stored as seconds
        assert m["release_date"] == "2007-02-15"
        assert m["origin_country"] == ["JP"]
        assert m["director"] == ["Hayato Date"]
        assert m["playwright"] == ["Junki Takegami"]
        assert m["producer"] == ["Studio Pierrot"]
        assert "Action" in m["genre"]
        titles = {t["text"] for t in m["localized_title"]}
        assert "NARUTO: Shippuuden" in titles
        assert "Naruto: Shippuden" in titles
        assert "NARUTO -ナルト- 疾風伝" in titles
        assert m["orig_title"] == "NARUTO -ナルト- 疾風伝"
        # description markup is flattened, not passed through
        assert "<br>" not in m["brief"]
        assert site.resource.get_all_lookup_ids().get(IdType.MAL_Anime) == "1735"
        assert isinstance(site.resource.item, TVSeason)

    @use_local_response
    def test_scrape_movie(self):
        site = SiteManager.get_site_by_url("https://anilist.co/anime/199")
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "Movie"
        assert m["length"] == 125 * 60
        assert m["director"] == ["Hayao Miyazaki"]
        assert m["release_date"] == "2001-07-20"
        assert "episode_count" not in m
        assert site.resource.get_all_lookup_ids().get(IdType.MAL_Anime) == "199"
        assert isinstance(site.resource.item, Movie)


@pytest.mark.django_db(databases="__all__")
class TestAniListManga:
    def test_parse(self):
        t_url = "https://anilist.co/manga/30011/NARUTO/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.AniList_Manga)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert not p1.validate_url("https://anilist.co/anime/1735")
        p2 = SiteManager.get_site_by_url(t_url)
        assert p2 is not None
        assert isinstance(p2, AniListManga)
        assert p2.id_value == "30011"
        assert p1.id_to_url("30011") == "https://anilist.co/manga/30011"

    @use_local_response
    def test_scrape(self):
        site = SiteManager.get_site_by_url("https://anilist.co/manga/30011")
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "Edition"
        assert m["author"] == ["Masashi Kishimoto"]
        assert m["pub_year"] == 1999
        assert m["pub_month"] == 9
        assert m["orig_title"] == "NARUTO -ナルト-"
        # Edition permits exactly one localized title; the rest go to other_title
        assert len(m["localized_title"]) == 1
        assert m["localized_title"][0]["text"] == "Naruto"
        assert "NARUTO -ナルト-" in m["other_title"]
        # AniList's manga id and MyAnimeList's differ; both must be kept straight
        assert site.resource.id_value == "30011"
        assert site.resource.get_all_lookup_ids().get(IdType.MAL_Manga) == "11"
        assert isinstance(site.resource.item, Edition)
