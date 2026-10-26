import logging

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test import Client

from catalog.models import Edition, Item, Movie
from catalog.search import CatalogIndex


def _edition() -> Edition:
    return Edition.objects.create(
        localized_title=[{"lang": "en", "text": "Orphan"}],
    )


def _drop_child_row(item: Item) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {item.__class__._meta.db_table} WHERE item_ptr_id = %s",
            [item.pk],
        )


@pytest.mark.django_db(databases="__all__")
class TestItemWithoutSubclassRow:
    """A base row whose child row is gone loads as a bare Item, which has no
    category, url_path or templates of its own."""

    def test_get_by_url_returns_none_and_logs(self, caplog):
        item = _edition()
        _drop_child_row(item)
        assert Item.objects.get(pk=item.pk).__class__ is Item
        with caplog.at_level(logging.ERROR, logger="catalog.models.item"):
            assert Item.get_by_url(item.uuid) is None
        assert "item has no subclass row" in caplog.text

    def test_get_by_url_keeps_intact_items(self):
        item = _edition()
        found = Item.get_by_url(item.uuid)
        assert isinstance(found, Edition)
        assert found.pk == item.pk

    def test_pages_answer_404(self):
        item = _edition()
        url = item.url
        _drop_child_row(item)
        client = Client()
        assert client.get(url).status_code == 404
        assert client.get(url + "/embed").status_code == 404
        response = client.get(url, HTTP_ACCEPT="application/activity+json")
        assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
def test_failed_recast_keeps_original_row(monkeypatch):
    item = _edition()

    def fail(*args, **kwargs):
        raise RuntimeError("recast failed")

    monkeypatch.setattr(QuerySet, "update", fail)
    with pytest.raises(RuntimeError):
        item.recast_to(Movie)
    monkeypatch.undo()
    found = Item.objects.get(pk=item.pk)
    assert isinstance(found, Edition)
    assert not Movie.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db(databases="__all__")
def test_recast_indexes_loaded_item(monkeypatch):
    item = _edition()
    indexed: list[tuple[type, str]] = []
    monkeypatch.setattr(
        CatalogIndex,
        "replace_item",
        lambda self, i: indexed.append((i.__class__, i.display_title)),
    )
    item.recast_to(Movie)
    assert indexed == [(Movie, "Orphan")]
