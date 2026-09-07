from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.test import override_settings
from PIL import Image

from catalog.models import (
    Album,
    CatalogCollection,
    Edition,
    Game,
    Item,
    Movie,
    People,
    Performance,
    PerformanceProduction,
    Podcast,
    PodcastEpisode,
    Series,
    TVEpisode,
    TVSeason,
    TVShow,
    Work,
)
from common.models.misc import MISSING_COVER
from common.templatetags.thumb import thumb


@pytest.mark.parametrize(
    "model, category, size",
    [
        (Edition, "book", (1200, 1800)),
        (Work, "book", (1200, 1800)),
        (Series, "book", (1200, 1800)),
        (Movie, "movie", (1200, 1800)),
        (TVShow, "tv", (1200, 1800)),
        (TVSeason, "tv", (1200, 1800)),
        (TVEpisode, "tv", (1200, 1800)),
        (Album, "music", (1200, 1200)),
        (Game, "game", (1200, 1800)),
        (Podcast, "podcast", (1200, 1200)),
        (PodcastEpisode, "podcast", (1200, 1200)),
        (Performance, "performance", (1200, 1800)),
        (PerformanceProduction, "performance", (1200, 1800)),
        (People, "people", (1200, 1800)),
        (CatalogCollection, "collection", (1200, 1200)),
    ],
)
def test_default_cover_matches_category(
    model: type[Item], category: str, size: tuple[int, int]
) -> None:
    name = f"img/default-cover-{category}.png"
    assert model().default_cover_image_url == f"https://example.org/s/{name}"
    path = finders.find(name)
    assert isinstance(path, str)
    with Image.open(Path(path)) as image:
        assert image.format == "PNG"
        assert image.size == size
        image.verify()


@pytest.mark.parametrize("cover", [MISSING_COVER, "", None])
def test_missing_artwork_uses_display_fallback_only(cover: str | None) -> None:
    item = Edition(cover=cover)
    assert not item.has_cover()
    assert item.cover_image_url is None
    assert item.display_cover_image_url.endswith("/img/default-cover-book.png")
    assert thumb(item.cover, "normal") == item.display_cover_image_url


def test_uploaded_artwork_takes_priority() -> None:
    item = Edition(cover="item/book.jpg")
    assert item.has_cover()
    assert item.display_cover_image_url == item.cover_image_url
    assert item.display_cover_image_url.endswith("/item/book.jpg")
    with patch(
        "common.templatetags.thumb.thumbnail_url", return_value="/m/thumb.jpg"
    ) as thumbnail:
        assert thumb(item.cover, "normal") == "/m/thumb.jpg"
        thumbnail.assert_called_once_with(item.cover, "normal")


def test_podcast_episode_artwork_priority() -> None:
    program = Podcast(cover="item/podcast.jpg")
    episode = PodcastEpisode(
        program=program, cover_url="https://example.com/episode.jpg"
    )
    assert episode.display_cover_image_url == "https://example.com/episode.jpg"
    episode.cover_url = None
    assert episode.display_cover_image_url == program.cover_image_url
    program.cover = MISSING_COVER
    assert episode.cover_image_url is None
    assert episode.display_cover_image_url == program.default_cover_image_url


@pytest.mark.parametrize("static_url", ["/assets/", "https://cdn.example.com/assets/"])
def test_default_cover_respects_static_url(static_url: str) -> None:
    with override_settings(STATIC_URL=static_url):
        expected = (
            static_url
            if static_url.startswith("https:")
            else "https://example.org" + static_url
        )
        assert Game().display_cover_image_url == expected + "img/default-cover-game.png"


def test_base_item_uses_generic_default() -> None:
    assert (
        Item().display_cover_image_url
        == "https://example.org" + settings.SITE_INFO["default_cover_url"]
    )


def test_default_cover_is_reused_across_items_in_the_same_category() -> None:
    with override_settings(STATIC_URL="/cached-assets/"):
        with patch("common.utils.static", return_value="/cached-cover.png") as resolve:
            for model in (Edition, Edition, Work, Series):
                assert (
                    model().default_cover_image_url
                    == "https://example.org/cached-cover.png"
                )
            resolve.assert_called_once_with("img/default-cover-book.png")
            Movie().default_cover_image_url
            assert resolve.call_count == 2


def test_default_cover_refreshes_when_site_settings_change() -> None:
    original_url = Game().default_cover_image_url
    with override_settings(
        SITE_INFO={**settings.SITE_INFO, "site_url": "https://other.example.org"}
    ):
        assert (
            Game().default_cover_image_url
            == "https://other.example.org/s/img/default-cover-game.png"
        )
    assert Game().default_cover_image_url == original_url


def test_thumbnail_template_uses_category_default() -> None:
    template = Template("{% load thumb %}<img src=\"{{ item.cover|thumb:'normal' }}\">")
    item = Album()
    assert (
        template.render(Context({"item": item}))
        == f'<img src="{item.display_cover_image_url}">'
    )


@pytest.mark.django_db
def test_work_inherits_edition_artwork_before_default() -> None:
    work = Work.objects.create(title="Book")
    assert work.display_cover_image_url == work.default_cover_image_url
    edition = Edition.objects.create(title="Edition", cover="item/edition.jpg")
    work.editions.add(edition)
    assert work.display_cover_image_url == edition.cover_image_url
