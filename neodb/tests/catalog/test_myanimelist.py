import asyncio

import pytest

from catalog.common import DownloadError, SiteManager, use_local_response
from catalog.models import Edition, IdType, ItemCategory, Movie, TVSeason
from catalog.sites.anilist import AniListAnime
from common.models import SiteConfig
from catalog.sites.myanimelist import (
    MyAnimeListAnime,
    MyAnimeListManga,
    _author_names,
    _localized,
    _parse_date,
    _titles,
    _year_month,
)


class TestUrl:
    def test_anime(self):
        cls = SiteManager.get_site_cls_by_id_type(IdType.MAL_Anime)
        assert cls is MyAnimeListAnime
        for url in (
            "https://myanimelist.net/anime/1735/Naruto__Shippuuden",
            "https://myanimelist.net/anime/1735",
            "https://myanimelist.net/anime.php?id=1735",
        ):
            assert cls.validate_url(url)
            assert cls.url_to_id(url) == "1735"
        assert not cls.validate_url("https://myanimelist.net/manga/11/Naruto")
        assert cls.id_to_url("1735") == "https://myanimelist.net/anime/1735"
        site = SiteManager.get_site_by_url("https://myanimelist.net/anime/1735/x")
        assert isinstance(site, MyAnimeListAnime)
        assert site.id_value == "1735"

    def test_manga(self):
        cls = SiteManager.get_site_cls_by_id_type(IdType.MAL_Manga)
        assert cls is MyAnimeListManga
        assert cls.validate_url("https://myanimelist.net/manga/11/Naruto")
        assert cls.validate_url("https://myanimelist.net/manga.php?id=11")
        assert not cls.validate_url("https://myanimelist.net/anime/1735")
        assert cls.id_to_url("11") == "https://myanimelist.net/manga/11"

    def test_api_url_names_a_stable_fixture(self):
        # the fixture file name is derived from the full url
        assert MyAnimeListAnime.api_url("1735").startswith(
            "https://api.myanimelist.net/v2/anime/1735?fields=id,title,"
        )
        assert len(MyAnimeListManga.api_url("11")) < 255


class TestDates:
    def test_only_full_dates_are_release_dates(self):
        assert _parse_date("2007-02-15") == "2007-02-15"
        assert _parse_date("2007-02") is None
        assert _parse_date("2007") is None
        assert _parse_date(None) is None

    def test_year_month(self):
        assert _year_month("1999-09-21") == (1999, 9)
        assert _year_month("1999-09") == (1999, 9)
        assert _year_month("1999") == (1999, None)
        assert _year_month(None) == (None, None)


class TestTitles:
    def test_english_owns_en_and_romaji_is_not_detected(self):
        node = {
            "title": "Naruto: Shippuuden",
            "alternative_titles": {
                "en": "Naruto: Shippuden",
                "ja": "NARUTO -ナルト- 疾風伝",
                "synonyms": ["Naruto Hurricane Chronicles"],
            },
        }
        entries = _localized(_titles(node))
        assert entries[0] == {"lang": "en", "text": "Naruto: Shippuden"}
        by_text = {e["text"]: e["lang"] for e in entries}
        assert by_text["Naruto: Shippuuden"] == "x"
        assert by_text["NARUTO -ナルト- 疾風伝"] == "ja"

    def test_romaji_stands_in_for_en(self):
        entries = _localized(_titles({"title": "Sen to Chihiro no Kamikakushi"}))
        assert entries == [{"lang": "en", "text": "Sen to Chihiro no Kamikakushi"}]

    def test_native_title_of_a_chinese_work(self):
        node = {"title": "Ling Long", "alternative_titles": {"ja": "灵笼"}}
        assert _titles(node)["灵笼"] == "zh-cn"


class TestAuthors:
    def test_full_name_and_dedupe(self):
        node = {
            "authors": [
                {
                    "node": {"first_name": "Masashi", "last_name": "Kishimoto"},
                    "role": "Story & Art",
                },
                {
                    "node": {"first_name": "Masashi", "last_name": "Kishimoto"},
                    "role": "Story",
                },
                {"node": {"first_name": "", "last_name": "CLAMP"}, "role": "Art"},
            ]
        }
        assert _author_names(node) == ["Masashi Kishimoto", "CLAMP"]


class TestUnconfigured:
    """The test SystemOptions carry no client id, as a fresh instance does."""

    def test_search_is_silent(self, monkeypatch):
        monkeypatch.setattr(
            SiteConfig, "system", SiteConfig.SystemOptions(mal_client_id="")
        )
        assert asyncio.run(MyAnimeListAnime.search_task("naruto", 1, "all", 10)) == []
        assert asyncio.run(MyAnimeListManga.search_task("naruto", 1, "book", 10)) == []

    def test_fetch_names_the_missing_setting(self, monkeypatch):
        monkeypatch.setattr(
            SiteConfig, "system", SiteConfig.SystemOptions(mal_client_id="")
        )
        site = SiteManager.get_site_by_url("https://myanimelist.net/anime/1735")
        assert site is not None
        # DownloadError, so the linked-resource fetch after an AniList import
        # logs a warning rather than an internal error
        with pytest.raises(DownloadError, match="Client ID"):
            site.scrape()


class TestSearchCategory:
    def test_single_category_searches_drop_the_other_type(self):
        wanted = MyAnimeListAnime._wanted
        assert wanted("movie", ItemCategory.Movie)
        assert not wanted("movie", ItemCategory.TV)
        assert wanted("tv", ItemCategory.TV)
        assert not wanted("tv", ItemCategory.Movie)
        for cat in ("all", "movietv"):
            assert wanted(cat, ItemCategory.Movie) and wanted(cat, ItemCategory.TV)

    def test_media_type_dispatch(self):
        assert MyAnimeListAnime._search_category({"media_type": "movie"}) == (
            ItemCategory.Movie
        )
        assert MyAnimeListAnime._search_category({"media_type": "music"}) == (
            ItemCategory.Movie
        )
        for t in ("tv", "ova", "ona", "special", None):
            assert MyAnimeListAnime._search_category({"media_type": t}) == (
                ItemCategory.TV
            )


@pytest.mark.django_db(databases="__all__")
class TestScrape:
    @use_local_response
    def test_tv(self):
        site = SiteManager.get_site_by_url("https://myanimelist.net/anime/1735")
        assert isinstance(site, MyAnimeListAnime)
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "TVSeason"
        assert m["episode_count"] == 500
        assert m["single_episode_length"] == 1389  # already seconds
        assert m["release_date"] == "2007-02-15"
        assert m["producer"] == ["Studio Pierrot"]
        assert "director" not in m  # v2 exposes no staff
        assert "Shounen" in m["genre"]
        by_text = {t["text"]: t["lang"] for t in m["localized_title"]}
        assert by_text["Naruto Shippuden"] == "en"
        assert by_text["Naruto: Shippuuden"] == "x"
        assert by_text["-ナルト- 疾風伝"] == "ja"
        assert by_text["Naruto Hurricane Chronicles"] == "x"
        assert m["orig_title"] == "-ナルト- 疾風伝"
        assert "[Written by MAL Rewrite]" in m["brief"]
        assert isinstance(site.resource.item, TVSeason)

    @use_local_response
    def test_movie(self):
        site = SiteManager.get_site_by_url("https://myanimelist.net/anime/199")
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "Movie"
        assert m["length"] == 7475
        assert "episode_count" not in m
        assert m["release_date"] == "2001-07-20"
        assert m["producer"] == ["Studio Ghibli"]
        assert isinstance(site.resource.item, Movie)

    @use_local_response
    def test_manga(self):
        site = SiteManager.get_site_by_url("https://myanimelist.net/manga/11/Naruto")
        assert isinstance(site, MyAnimeListManga)
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["preferred_model"] == "Edition"
        assert m["localized_title"] == [{"lang": "en", "text": "Naruto"}]
        assert m["other_title"] == ["NARUTO―ナルト―"]
        assert m["orig_title"] == "NARUTO―ナルト―"
        assert m["author"] == ["Masashi Kishimoto"]
        assert m["pub_year"] == 1999
        assert m["pub_month"] == 9
        assert isinstance(site.resource.item, Edition)

    @use_local_response
    def test_lands_on_the_item_anilist_created(self):
        """AniList files idMal as a MAL_Anime lookup id; the MAL fetch must
        match that item instead of creating a duplicate."""
        anilist = SiteManager.get_site_by_url("https://anilist.co/anime/1735")
        assert isinstance(anilist, AniListAnime)
        anilist.get_resource_ready()
        assert anilist.resource is not None
        assert anilist.resource.other_lookup_ids.get(IdType.MAL_Anime) == "1735"

        mal = SiteManager.get_site_by_url("https://myanimelist.net/anime/1735")
        assert mal is not None
        mal.get_resource_ready()
        assert mal.resource is not None
        assert mal.resource.item == anilist.resource.item
        assert TVSeason.objects.count() == 1
        # the AniList director credit survives the merge
        assert anilist.resource.item.director == ["Hayato Date"]
