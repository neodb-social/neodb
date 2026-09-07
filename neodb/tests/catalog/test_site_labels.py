import re

import pytest
from django.test import Client

from catalog.models import Edition, ExternalResource, IdType


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
