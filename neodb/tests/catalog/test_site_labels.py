import re

import pytest
from django.test import Client
from django.urls import reverse

from catalog.models import Edition, ExternalResource, IdType, ItemCategory, SiteName
from catalog.search.external import ExternalSearchResultItem, ExternalSources
from users.models import User


def _edition_with_resources(title: str, id_types: list[IdType]) -> Edition:
    item = Edition.objects.create(title=title)
    for n, id_type in enumerate(id_types):
        value = f"{title}-{n}"
        if id_type == IdType.Fediverse:
            value = f"https://fedi.example/items/{n}"
        elif id_type == IdType.WikiData:
            value = f"Q{n}"
        ExternalResource.objects.create(
            item=item,
            id_type=id_type,
            id_value=value,
            url=f"https://example.org/{value}",
        )
    return item


def _label_classes(html: str) -> list[str]:
    block = re.search(r'<span class="site-list[^"]*">(.*?)</span>', html, re.S)
    assert block
    return re.findall(r'class="([^"]+)"', block.group(1))


@pytest.mark.django_db(databases="__all__")
def test_item_page_collapses_to_three_from_five_labels():
    item = _edition_with_resources(
        "many",
        [
            IdType.DoubanBook,
            IdType.Goodreads,
            IdType.GoogleBooks,
            IdType.BooksTW,
            IdType.OpenLibrary,
            IdType.Readmoo,
        ],
    )
    html = Client().get(item.url).content.decode()
    assert 'class="site-list collapsed"' in html
    assert html.count(' extra"') == 3
    assert ">+3</a>" in html


@pytest.mark.django_db(databases="__all__")
def test_item_page_keeps_four_labels_flat():
    item = _edition_with_resources(
        "four",
        [IdType.DoubanBook, IdType.Goodreads, IdType.GoogleBooks, IdType.BooksTW],
    )
    html = Client().get(item.url).content.decode()
    assert 'class="site-list"' in html
    assert "collapsed" not in html
    assert ' extra"' not in html
    assert 'class="more"' not in html


@pytest.mark.django_db(databases="__all__")
def test_labels_order_priority_sites_first_and_fediverse_last():
    item = _edition_with_resources(
        "order",
        [
            IdType.Fediverse,
            IdType.BooksTW,
            IdType.GoogleBooks,
            IdType.DoubanBook,
            IdType.WikiData,
        ],
    )
    html = Client().get(item.url).content.decode()
    assert _label_classes(html) == [
        "wikidata",
        "googlebooks",
        "bookstw",
        "douban extra",
        "fedi extra",
        "more",
    ]


@pytest.mark.django_db(databases="__all__")
def test_external_search_results_render_site_labels(monkeypatch):
    """Results not saved locally carry a stand-in resource, not a plain dict."""
    results = [
        ExternalSearchResultItem(
            ItemCategory.Movie,
            SiteName.TMDB,
            "https://www.themoviedb.org/movie/1",
            "Ext Movie",
            "",
            "brief",
            "",
        ),
        ExternalSearchResultItem(
            ItemCategory.Movie,
            "peer.example",
            "https://peer.example/movies/2",
            "Peer Movie",
            "",
            "brief",
            "",
        ),
    ]
    monkeypatch.setattr(
        ExternalSources, "search", classmethod(lambda cls, *a, **kw: results)
    )
    user = User.register(email="ext@example.com", username="extuser")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    response = client.get(reverse("catalog:external_search"), {"q": "movie"})
    assert response.status_code == 200
    html = response.content.decode()
    assert '<a href="https://www.themoviedb.org/movie/1"' in html
    assert 'class="tmdb"' in html
    assert ">TMDB</a>" in html
    assert 'class="fedi"' in html
    assert ">peer.example</a>" in html
