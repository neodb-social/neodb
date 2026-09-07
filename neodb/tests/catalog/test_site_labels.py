import pytest
from django.test import Client

from catalog.models import Edition, ExternalResource, IdType


def _edition_with_resources(title: str, id_types: list[IdType]) -> Edition:
    item = Edition.objects.create(title=title)
    for n, id_type in enumerate(id_types):
        ExternalResource.objects.create(
            item=item,
            id_type=id_type,
            id_value=f"{title}-{n}",
            url=f"https://example.org/{title}/{n}",
        )
    return item


@pytest.mark.django_db(databases="__all__")
def test_item_page_collapses_labels_past_three():
    item = _edition_with_resources(
        "many",
        [
            IdType.DoubanBook,
            IdType.Goodreads,
            IdType.GoogleBooks,
            IdType.BooksTW,
            IdType.OpenLibrary,
        ],
    )
    html = Client().get(item.url).content.decode()
    assert 'class="site-list collapsed"' in html
    assert html.count(' extra"') == 2
    assert ">+2</a>" in html


@pytest.mark.django_db(databases="__all__")
def test_item_page_keeps_three_labels_flat():
    item = _edition_with_resources(
        "three", [IdType.DoubanBook, IdType.Goodreads, IdType.GoogleBooks]
    )
    html = Client().get(item.url).content.decode()
    assert 'class="site-list"' in html
    assert "collapsed" not in html
    assert ' extra"' not in html
    assert 'class="more"' not in html
