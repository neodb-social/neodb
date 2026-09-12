import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from catalog.common import SiteManager
from catalog.models import Edition, IdType, SiteName
from catalog.sites.google_books import GoogleBooks
from common.models import SiteConfig


@pytest.mark.parametrize(
    "url",
    [
        "https://books.google.com/books?id=Fse0EAAAQBAJ",
        "https://books.google.com/books?hl=en&source=gbs_api&id=Fse0EAAAQBAJ&pg=PP1#v=onepage",
        "http://books.google.com/books?hl=en&id=Fse0EAAAQBAJ&source=gbs_api#v=onepage",
        "https://books.google.com.bn/books?hl=ms&id=Fse0EAAAQBAJ",
        "https://books.google.co.uk/books/about/The_Hobbit.html?hl=en&id=Fse0EAAAQBAJ",
        "https://www.google.com/books/edition/The_Hobbit/Fse0EAAAQBAJ?gbpv=1",
        "https://www.google.com/books/edition/_/Fse0EAAAQBAJ?kptab=editions",
        "https://books.google.com/books/edition/_/Fse0EAAAQBAJ/",
        "https://play.google.com/store/books/details?pcampaignid=books_read_action&id=Fse0EAAAQBAJ",
        "https://play.google.com/store/books/details/The_Hobbit?hl=en_US&id=Fse0EAAAQBAJ",
        "http://play.google.com/books/reader?hl=en&id=Fse0EAAAQBAJ&pg=GBS.PP1",
    ],
)
def test_volume_urls(url):
    assert SiteManager.get_class_by_url(url) is GoogleBooks
    site = GoogleBooks(url)
    assert site.id_value == "Fse0EAAAQBAJ"
    assert site.url == "https://books.google.com/books?id=Fse0EAAAQBAJ"


@pytest.mark.parametrize(
    "url",
    [
        "https://play.google.com/store/audiobooks/details?id=AQAAAACHmnea4M",
        "https://play.google.com/store/books/series?id=5aqsHAAAABBItM",
        "https://play.google.com/store/apps/details?id=org.example.app",
        "https://books.google.com/books?vid=ISBN0451522907",
        "https://books.google.com/books?fakeid=Fse0EAAAQBAJ",
        "https://books.google.com/books#id=Fse0EAAAQBAJ",
        "https://books.google.com/books?id=",
        "https://books.google.com/books?id=one&id=two",
        "https://books.google.com/books?hl=en&id=one&source=gbs_api&id=two",
        "https://books.google.com/books?id=&id=Fse0EAAAQBAJ",
        "https://books.google.com/books?hl=en#fragment&id=Fse0EAAAQBAJ",
        "https://books.google.com/books?id=Fse0EAAAQBAJ/other",
        "https://books.google.com/books?id=..%2Fother",
        "https://books.google.com/books/edition/_/Fse0EAAAQBAJ/other",
        "https://books.google.com.example.org/books?id=Fse0EAAAQBAJ",
        "https://books.google.example/books?id=Fse0EAAAQBAJ",
        "https://play.google.com@evil.example/books?id=Fse0EAAAQBAJ",
    ],
)
def test_non_volume_urls(url):
    assert GoogleBooks.url_to_id(url) is None
    assert not GoogleBooks.validate_url(url)


@pytest.fixture
def volume():
    return {
        "id": "Fse0EAAAQBAJ",
        "volumeInfo": {
            "title": "The Hobbit",
            "language": "en",
            "categories": ["Fiction / Fantasy", "Juvenile Fiction"],
            "printedPageCount": 432,
            "imageLinks": {"medium": "http://books.google.com/cover.jpg"},
        },
        "searchInfo": {"textSnippet": "A <b>hobbit</b> &amp; friends.<br>Adventure."},
        "saleInfo": {"isEbook": True},
    }


def test_scrape_metadata(monkeypatch, volume):
    downloader = Mock()
    downloader.download.return_value.json.return_value = volume
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicDownloader", lambda url: downloader
    )
    image = Mock(return_value=(None, None))
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicImageDownloader.download_image", image
    )
    content = GoogleBooks(id_value=volume["id"]).scrape()
    assert content.metadata["format"] == Edition.BookFormat.EBOOK
    assert content.metadata["pages"] == 432
    assert (
        content.metadata["other_info"]["分类"] == "Fiction / Fantasy; Juvenile Fiction"
    )
    assert content.metadata["localized_description"] == [
        {"lang": "en", "text": "A hobbit & friends.\nAdventure."}
    ]
    image.assert_called_once_with(
        "https://books.google.com/cover.jpg", None, headers={}
    )


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("OCLC:1194269685", "1194269685"),
        ("OCLC:759068128", "759068128"),
        ("UOM:39015020073196", None),
        ("LCCN:96072233", None),
        ("1194269685", None),
        ("OCLC:", None),
        ("OCLC:123abc", None),
    ],
)
def test_scrape_oclc_identifier(monkeypatch, volume, identifier, expected):
    volume["volumeInfo"]["industryIdentifiers"] = [
        {"type": "ISBN_13", "identifier": "9780063347540"},
        {"type": "OTHER", "identifier": identifier},
        {"type": "OTHER", "identifier": "HARVARD:HN6JXY"},
    ]
    downloader = Mock()
    downloader.download.return_value.json.return_value = volume
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicDownloader", lambda url: downloader
    )
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicImageDownloader.download_image",
        lambda *args, **kwargs: (None, None),
    )
    content = GoogleBooks(id_value=volume["id"]).scrape()
    assert content.lookup_ids[IdType.ISBN] == "9780063347540"
    if expected:
        assert content.lookup_ids[IdType.OCLC] == expected
    else:
        assert IdType.OCLC not in content.lookup_ids

    # Older volumes may have an OCLC number without an ISBN.
    volume["volumeInfo"]["industryIdentifiers"].pop(0)
    content = GoogleBooks(id_value=volume["id"]).scrape()
    assert content.lookup_ids.get(IdType.ISBN) is None
    assert content.lookup_ids.get(IdType.OCLC) == expected


def test_scrape_unknown_format_and_category_fallback(monkeypatch, volume):
    volume["saleInfo"]["isEbook"] = False
    volume["volumeInfo"].pop("categories")
    volume["volumeInfo"].update(mainCategory="Fiction", pageCount=400)
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicDownloader",
        lambda url: SimpleNamespace(
            download=lambda: SimpleNamespace(json=lambda: volume)
        ),
    )
    monkeypatch.setattr(
        "catalog.sites.google_books.BasicImageDownloader.download_image",
        lambda *args, **kwargs: (None, None),
    )
    content = GoogleBooks(id_value=volume["id"]).scrape()
    assert "format" not in content.metadata
    assert content.metadata["pages"] == 400
    assert content.metadata["other_info"]["分类"] == "Fiction"


@pytest.mark.parametrize(
    "size", ["extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"]
)
def test_cover_fallback_sizes(volume, size):
    volume["volumeInfo"]["imageLinks"] = {size: "http://books.google.com/cover.jpg"}
    assert GoogleBooks._cover_url(volume) == "https://books.google.com/cover.jpg"
    assert (
        GoogleBooks._cover_url(volume, thumbnail=True)
        == "https://books.google.com/cover.jpg"
    )


def test_description_precedence_and_cover_preferences(volume):
    volume["volumeInfo"]["description"] = "<p>Full &lt;story&gt;.</p><p>More.</p>"
    assert GoogleBooks._description(volume) == "Full <story>.\nMore."
    volume["volumeInfo"]["imageLinks"]["thumbnail"] = (
        "https://books.google.com/thumb.jpg"
    )
    assert GoogleBooks._cover_url(volume) == "https://books.google.com/cover.jpg"
    assert (
        GoogleBooks._cover_url(volume, thumbnail=True)
        == "https://books.google.com/thumb.jpg"
    )


@pytest.mark.parametrize("api_key", ["", "test-key"])
def test_search_key_and_metadata(monkeypatch, volume, api_key):
    monkeypatch.setattr(SiteConfig.system, "google_api_key", api_key)
    requests = []

    async def get(self, url, **kwargs):
        requests.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "items": [
                    volume,
                    {
                        "id": "no-cover",
                        "volumeInfo": {"title": "No cover", "imageLinks": {}},
                    },
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    results = asyncio.run(GoogleBooks.search_task("hobbit & friends", 2, "book", 3))
    params = parse_qs(urlsplit(requests[0]).query)
    assert params.get("key") == ([api_key] if api_key else None)
    assert params["q"] == ["hobbit & friends"]
    assert params["startIndex"] == ["3"]
    assert params["maxResults"] == ["3"]
    assert len(results) == 2
    assert results[0].display_description == "A hobbit & friends.\nAdventure."
    assert results[0].cover_image_url == "https://books.google.com/cover.jpg"
    assert results[1].cover_image_url == ""


@pytest.mark.parametrize("status", [403, 429, 500])
def test_search_http_error_recorded_without_key(monkeypatch, caplog, status):
    monkeypatch.setattr(SiteConfig.system, "google_api_key", "secret-test-key")

    async def get(self, url, **kwargs):
        return httpx.Response(
            status, request=httpx.Request("GET", url), json={"error": {}}
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    failure = Mock()
    monkeypatch.setattr("catalog.sites.google_books.record_search_failure", failure)
    assert asyncio.run(GoogleBooks.search_task("hobbit", 1, "book", 3)) == []
    failure.assert_called_once_with(SiteName.GoogleBooks.value, "error")
    assert "secret-test-key" not in caplog.text
    assert all(
        "secret-test-key" not in str(record.__dict__) for record in caplog.records
    )


@pytest.mark.parametrize("item", [None, "invalid", {"volumeInfo": None}])
def test_search_malformed_item_does_not_abort_other_sources(monkeypatch, item):
    async def get(self, url, **kwargs):
        return httpx.Response(
            200, request=httpx.Request("GET", url), json={"items": [item]}
        )

    async def healthy_source():
        return ["healthy-source-result"]

    async def search_sources():
        return await asyncio.gather(
            GoogleBooks.search_task("hobbit", 1, "book", 3), healthy_source()
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    failure = Mock()
    monkeypatch.setattr("catalog.sites.google_books.record_search_failure", failure)
    assert asyncio.run(search_sources()) == [[], ["healthy-source-result"]]
    failure.assert_called_once_with(SiteName.GoogleBooks.value, "error")
