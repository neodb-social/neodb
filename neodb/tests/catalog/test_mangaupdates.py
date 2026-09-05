import json

import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, IdType
from catalog.sites.mangaupdates import (
    MangaUpdates,
    _authors,
    _description,
    _orig_title,
)

NARUTO = "7z3yqqk"  # series_id 17360452316
BERSERK = "njeqwry"  # series_id 51239621230
NARUTO_NOVEL = "k0z4zlu"  # series_id 43594666050


def _fixture(series_id: int) -> dict:
    with open(f"test_data/https___api_mangaupdates_com_v1_series_{series_id}") as f:
        return json.load(f)


class TestUrl:
    def test_slug_is_the_id(self):
        cls = SiteManager.get_site_cls_by_id_type(IdType.MangaUpdates)
        assert cls is MangaUpdates
        for url in (
            "https://www.mangaupdates.com/series/7z3yqqk/naruto",
            "https://www.mangaupdates.com/series/7z3yqqk",
            "https://mangaupdates.com/series/7z3yqqk/",
        ):
            assert cls.validate_url(url)
            assert cls.url_to_id(url) == NARUTO
        assert cls.id_to_url(NARUTO) == "https://www.mangaupdates.com/series/7z3yqqk"

    def test_legacy_numeric_urls_are_not_matched(self):
        # a different id space that the API no longer serves
        assert not MangaUpdates.validate_url(
            "https://www.mangaupdates.com/series.html?id=33"
        )
        assert not MangaUpdates.validate_url("https://www.mangaupdates.com/authors/x")

    def test_api_url_decodes_base36(self):
        assert MangaUpdates.api_url(NARUTO) == (
            "https://api.mangaupdates.com/v1/series/17360452316"
        )


class TestDescription:
    def test_drops_link_blocks_and_markdown(self):
        text = _description(_fixture(17360452316)["description"])
        assert text.startswith("From Viz:\nTwelve years ago")
        assert "Official Translations" not in text
        assert "Original Manga" not in text
        assert "](" not in text
        assert "**" not in text

    def test_keeps_bold_prose(self):
        text = _description(_fixture(51239621230)["description"])
        assert "Warning: For mature audiences only" in text

    def test_empty(self):
        assert _description(None) == ""


class TestAuthors:
    def test_dedupes_the_same_person_across_roles(self):
        # Kishimoto is listed as Author and again as Artist, spelt differently
        assert _authors(_fixture(17360452316)) == ["Kishimoto Masashi"]

    def test_author_before_artist(self):
        assert _authors(_fixture(43594666050)) == [
            "KUSAKABE Masatoshi",
            "KISHIMOTO Masashi",
        ]


class TestOrigTitle:
    def test_manga_requires_kana(self):
        # Berserk's associated titles include Chinese ones; kana picks Japanese
        assert _orig_title(_fixture(51239621230)) == "ベルセルク"
        assert _orig_title(_fixture(17360452316)) == "NARUTO―ナルト―"

    def test_manhwa_and_manhua_scripts(self):
        assoc = [
            {"title": "ベルセルク"},
            {"title": "烙印勇士"},
            {"title": "베르세르크"},
        ]
        assert _orig_title({"type": "Manhwa", "associated": assoc}) == "베르세르크"
        assert _orig_title({"type": "Manhua", "associated": assoc}) == "烙印勇士"

    def test_untyped_has_none(self):
        assert (
            _orig_title({"type": "Novel", "associated": [{"title": "ナルト"}]}) is None
        )


@pytest.mark.django_db(databases="__all__")
class TestScrape:
    @use_local_response
    def test_manga(self):
        site = SiteManager.get_site_by_url(
            "https://www.mangaupdates.com/series/7z3yqqk/naruto"
        )
        assert isinstance(site, MangaUpdates)
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.id_type == IdType.MangaUpdates
        assert site.resource.id_value == NARUTO
        m = site.resource.metadata
        assert m["preferred_model"] == "Edition"
        assert m["localized_title"] == [{"lang": "en", "text": "Naruto"}]
        assert m["orig_title"] == "NARUTO―ナルト―"
        assert "Наруто" in m["other_title"]
        assert "Naruto" not in m["other_title"]
        assert m["author"] == ["Kishimoto Masashi"]
        assert m["publisher"] == ["Shueisha"]
        assert m["pub_year"] == 1999
        assert m["language"] == ["ja"]
        assert "Shounen" in m["genre"]
        assert m["brief"].startswith("From Viz:")
        assert m["cover_image_url"] == "https://cdn.mangaupdates.com/image/i140134.png"
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.display_title == "Naruto"

    @use_local_response
    def test_novel_carries_no_language(self):
        site = SiteManager.get_site_by_url(
            "https://www.mangaupdates.com/series/k0z4zlu/naruto-novel"
        )
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["pub_year"] == 2002
        assert "language" not in m
        assert "orig_title" not in m
        assert m["other_title"] == ["ナルト (Novel)"]
