import json

import pytest

from catalog.common import ParseError, SiteManager, use_local_response
from catalog.models import Edition, IdType, Movie, TVSeason
from catalog.sites.anilist import (
    AniListAnime,
    AniListManga,
    _base_role,
    _localized,
    _titles,
)


def _fixture(media_id: int) -> dict:
    with open(f"test_data/anilist_media_{media_id}") as f:
        return json.load(f)["data"]["Media"]


class TestTitleLanguages:
    def test_romaji_language_is_assigned_not_detected(self):
        # langdetect reads "NARUTO: Shippuuden" as Finnish and "Sen to Chihiro
        # no Kamikakushi" as Swahili, so romaji must never be detected.
        for media_id, romaji in (
            (1735, "NARUTO: Shippuuden"),
            (199, "Sen to Chihiro no Kamikakushi"),
        ):
            entries = _localized(_titles(_fixture(media_id)))
            lang = next(e["lang"] for e in entries if e["text"] == romaji)
            assert lang == "x"  # English title exists, so romaji is unknown

    def test_english_title_owns_en(self):
        entries = _localized(_titles(_fixture(1735)))
        assert entries[0] == {"lang": "en", "text": "Naruto: Shippuden"}

    def test_romaji_stands_in_for_en_without_english_title(self):
        media = _fixture(1735)
        media["title"]["english"] = None
        entries = _localized(_titles(media))
        assert entries[0] == {"lang": "en", "text": "NARUTO: Shippuuden"}

    def test_no_two_titles_claim_the_same_language(self):
        for media_id in (1735, 199, 30011):
            entries = _localized(_titles(_fixture(media_id)))
            langs = [e["lang"] for e in entries if e["lang"] != "x"]
            assert len(langs) == len(set(langs))

    def test_native_title_uses_country_of_origin(self):
        entries = _localized(_titles(_fixture(199)))
        lang = next(e["lang"] for e in entries if e["text"] == "千と千尋の神隠し")
        assert lang == "ja"


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


class TestAuthorRoles:
    def test_only_exact_author_roles_count(self):
        from catalog.sites.anilist import _AUTHOR_ROLES, _staff_names

        media = {
            "staff": {
                "edges": [
                    {"role": "Story & Art", "node": {"name": {"full": "Real Author"}}},
                    {"role": "Art Director", "node": {"name": {"full": "Not Author"}}},
                    {"role": "Story Editor", "node": {"name": {"full": "Also Not"}}},
                    {"role": "Art Assistant", "node": {"name": {"full": "Nope"}}},
                ]
            }
        }
        assert _staff_names(media, lambda r: r in _AUTHOR_ROLES) == ["Real Author"]


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

    @use_local_response
    def test_rejects_a_manga_id_under_the_anime_path(self):
        """anilist.co/anime/30011 is really a manga id.

        AniList keeps anime and manga in one id space, so without a type check
        this would build a TVSeason from manga data and file the manga's
        idMal (11) as IdType.MAL_Anime, corrupting MyAnimeList dedupe.
        """
        site = SiteManager.get_site_by_url("https://anilist.co/anime/30011")
        assert isinstance(site, AniListAnime)
        with pytest.raises(ParseError):
            site.scrape()


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
