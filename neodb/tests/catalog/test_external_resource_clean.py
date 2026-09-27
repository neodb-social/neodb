import pytest

from catalog.common.sites import ResourceContent
from catalog.models import ExternalResource, IdType
from common.utils import clean_json


def test_clean_json_returns_same_object_when_clean():
    data = {"title": "\U0001f600", "list": [1, 2.5, None, {"a": "b"}]}
    assert clean_json(data) is data


@pytest.mark.django_db(databases="__all__")
def test_update_content_cleans_values_postgres_rejects():
    resource = ExternalResource(
        id_type=IdType.IMDB,
        id_value="tt0000001",
        url="https://www.imdb.com/title/tt0000001/",
    )
    rc = ResourceContent(
        lookup_ids={IdType.ISBN: "97800000\x0000000"},
        metadata={
            "title": "Ti\x00tle",
            "brief": "half an emoji \ud83d",
            "localized_title": [{"lang": "en", "text": "a\x00b"}],
            "rating": float("nan"),
            "preferred_model": "Movie",
        },
    )
    resource.update_content(rc)
    resource.refresh_from_db()
    assert resource.other_lookup_ids == {IdType.ISBN: "9780000000000"}
    assert resource.metadata == {
        "title": "Title",
        "brief": "half an emoji �",
        "localized_title": [{"lang": "en", "text": "ab"}],
        "rating": None,
        "preferred_model": "Movie",
    }
