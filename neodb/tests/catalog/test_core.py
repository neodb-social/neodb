import httpx
import pytest

from catalog.core import (
    CatalogRef,
    CoreClient,
    CoreDegradedError,
    CoreErrorKind,
    CoreNotFoundError,
)

BASE_URL = "https://catalog-core.example.test"


def ref_payload(source_id=1, entity_type="release"):
    return {
        "ref": f"discogs:{entity_type}:{source_id}",
        "namespace": "discogs",
        "entity_type": entity_type,
        "source_id": source_id,
    }


def search_payload(**overrides):
    result = {
        "ref": ref_payload(),
        "title": "Unknown Pressing",
        "artists": [
            {
                "ref": ref_payload(42, "artist"),
                "display_name": "An Artist",
                "anv": None,
                "join_text": None,
            }
        ],
        "released": "1997",
        "country": None,
    }
    result.update(overrides)
    return {
        "query": {"q": "An Artist Unknown Pressing", "limit": 20},
        "results": [result],
    }


def detail_payload():
    return {
        "ref": ref_payload(),
        "title": "Unknown Pressing",
        "country": None,
        "released": "1997",
        "data_quality": "UNKNOWN",
        "master_ref": None,
        "is_main_release_text": None,
        "genres": [],
        "styles": [],
        "artists": [
            {
                "ref": ref_payload(42, "artist"),
                "display_name": "An Artist",
                "anv": None,
                "join_text": None,
            }
        ],
        "formats": [
            {
                "name": "Vinyl",
                "quantity_text": None,
                "text": None,
                "descriptions": [],
            }
        ],
        "identifiers": [{"type": "Barcode", "value": None, "description": None}],
        "provider_occurrences": [],
        "tracks": [],
        "notes": None,
        "extra_credits": [],
        "videos": [],
        "artwork": {
            "association": None,
            "display": {
                "status": "unavailable",
                "strategy": None,
                "provider": None,
                "content_url": None,
                "master_ref": None,
            },
        },
    }


def test_catalog_ref_round_trips_and_rejects_mismatched_payload():
    ref = CatalogRef.parse("discogs:release:123")

    assert ref == CatalogRef("discogs", "release", 123)
    assert str(ref) == "discogs:release:123"
    assert CatalogRef.from_payload(ref_payload(123)) == ref
    with pytest.raises(ValueError, match="do not agree"):
        CatalogRef.from_payload({**ref_payload(123), "ref": "discogs:release:124"})


def test_search_mapping_preserves_partial_fields(httpx_mock):
    httpx_mock.add_response(json=search_payload())

    with CoreClient(BASE_URL) as client:
        results = client.search_releases("An Artist Unknown Pressing")

    assert results[0].ref == CatalogRef("discogs", "release", 1)
    assert results[0].artists[0].display_name == "An Artist"
    assert results[0].released == "1997"
    assert results[0].country is None
    request = httpx_mock.get_request()
    assert request.url.path == "/api/search/releases/artist-title"
    assert dict(request.url.params) == {
        "q": "An Artist Unknown Pressing",
        "limit": "20",
    }


def test_detail_mapping_preserves_unknown_and_artwork_state(httpx_mock):
    httpx_mock.add_response(json=detail_payload())

    with CoreClient(BASE_URL) as client:
        detail = client.get_release(CatalogRef("discogs", "release", 1))

    assert detail.data_quality == "UNKNOWN"
    assert detail.released == "1997"
    assert detail.country is None
    assert detail.artwork.status == "unavailable"
    assert detail.formats[0].name == "Vinyl"
    assert httpx_mock.get_request().url.path == "/api/releases/1"


def test_404_is_explicit_not_found(httpx_mock):
    httpx_mock.add_response(status_code=404, json={"error": "release_not_found"})

    with CoreClient(BASE_URL) as client, pytest.raises(CoreNotFoundError) as error:
        client.get_release(CatalogRef("discogs", "release", 999999999))

    assert error.value.kind is CoreErrorKind.NOT_FOUND
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    ("exception", "kind"),
    [
        (httpx.ConnectTimeout("connect"), CoreErrorKind.CONNECT_TIMEOUT),
        (httpx.ReadTimeout("read"), CoreErrorKind.READ_TIMEOUT),
        (httpx.ConnectError("connection"), CoreErrorKind.CONNECTION),
    ],
)
def test_transport_failures_are_bounded_degraded_errors(httpx_mock, exception, kind):
    httpx_mock.add_exception(exception)

    with CoreClient(BASE_URL) as client, pytest.raises(CoreDegradedError) as error:
        client.search_releases("query")

    assert error.value.kind is kind
    assert error.value.status_code is None


def test_5xx_is_unavailable_not_empty_success(httpx_mock):
    httpx_mock.add_response(status_code=503, json={"error": "unavailable"})

    with CoreClient(BASE_URL) as client, pytest.raises(CoreDegradedError) as error:
        client.search_releases("query")

    assert error.value.kind is CoreErrorKind.UNAVAILABLE
    assert error.value.status_code == 503


def test_invalid_response_is_degraded(httpx_mock):
    httpx_mock.add_response(status_code=200, content=b"not-json")

    with CoreClient(BASE_URL) as client, pytest.raises(CoreDegradedError) as error:
        client.search_releases("query")

    assert error.value.kind is CoreErrorKind.INVALID_RESPONSE


def test_invalid_mapped_payload_is_not_silently_accepted(httpx_mock):
    httpx_mock.add_response(json={"results": [{"ref": {}}]})

    with CoreClient(BASE_URL) as client, pytest.raises(CoreDegradedError) as error:
        client.search_releases("query")

    assert error.value.kind is CoreErrorKind.INVALID_RESPONSE
