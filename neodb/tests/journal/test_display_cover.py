from unittest.mock import patch

import pytest
from django.contrib.staticfiles import finders
from django.test import override_settings
from PIL import Image

from catalog.models import CatalogCollection, Edition, Podcast, PodcastEpisode
from common.models.misc import MISSING_COVER
from common.templatetags.thumb import thumb
from journal.models import Article, Collection, Review


@pytest.mark.parametrize(
    "model, size",
    [(Article, (2400, 1200)), (Collection, (1200, 1200)), (Review, (2400, 1200))],
)
def test_journal_default_cover_asset(
    model: type[Article | Collection | Review], size: tuple[int, int]
) -> None:
    name = f"img/default-cover-{model.url_path}.png"
    assert model().default_cover_image_url == f"https://example.org/s/{name}"
    path = finders.find(name)
    assert isinstance(path, str)
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == size
        image.verify()


@pytest.mark.parametrize("model", [Article, Collection])
@pytest.mark.parametrize("cover", [MISSING_COVER, "", None])
def test_missing_journal_cover(
    model: type[Article | Collection], cover: str | None
) -> None:
    piece = model(cover=cover)
    assert piece.cover_image_url is None
    assert piece.display_cover_image_url == piece.default_cover_image_url
    assert thumb(piece.cover, "normal") == piece.default_cover_image_url


@pytest.mark.parametrize("model", [Article, Collection])
def test_uploaded_journal_cover_takes_priority(
    model: type[Article | Collection],
) -> None:
    piece = model(cover="piece/cover.jpg")
    assert piece.display_cover_image_url == piece.cover_image_url
    assert piece.display_cover_image_url.endswith("/piece/cover.jpg")


def test_article_remote_cover_takes_priority_over_default() -> None:
    article = Article(metadata={"cover_url": "https://remote.example/cover.jpg"})
    assert article.display_cover_image_url == "https://remote.example/cover.jpg"
    assert thumb(article.cover, "normal") == article.display_cover_image_url
    article.cover = "piece/local.jpg"
    assert article.display_cover_image_url.endswith("/piece/local.jpg")


@pytest.mark.parametrize("cover", [MISSING_COVER, "", None])
def test_review_uses_its_own_default_without_item_artwork(cover: str | None) -> None:
    item = Edition(cover=cover)
    review = Review(item=item)
    assert review.cover_image_url is None
    assert review.display_cover_image_url == review.default_cover_image_url
    assert review.display_cover_image_url != item.display_cover_image_url


def test_review_uses_item_artwork_when_available() -> None:
    item = Edition(cover="item/book.jpg")
    review = Review(item=item)
    assert review.cover == item.cover
    assert review.display_cover_image_url == item.cover_image_url


def test_review_preserves_podcast_artwork_inheritance() -> None:
    program = Podcast(cover="item/podcast.jpg")
    review = Review(item=PodcastEpisode(program=program))
    assert review.display_cover_image_url == program.cover_image_url
    program.cover = MISSING_COVER
    assert review.display_cover_image_url == review.default_cover_image_url


def test_collection_default_cache_is_shared_with_catalog() -> None:
    with override_settings(STATIC_URL="/cached-assets/"):
        with patch("common.utils.static", return_value="/collection.png") as resolve:
            assert (
                Collection().default_cover_image_url
                == CatalogCollection().default_cover_image_url
            )
            assert (
                Collection().display_cover_image_url
                == "https://example.org/collection.png"
            )
            resolve.assert_called_once_with("img/default-cover-collection.png")
