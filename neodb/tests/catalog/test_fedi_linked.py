"""A peer item's linked resources are resolved before it can create an item."""

from unittest.mock import MagicMock, patch

import pytest

from catalog.common import ResourceContent
from catalog.common.downloaders import (
    RESPONSE_NETWORK_ERROR,
    BasicDownloader,
    DownloadError,
    RetryDownloader,
    short_requests,
)
from catalog.common.sites import SiteManager
from catalog.models import Album, Edition, ExternalResource, IdType
from catalog.sites.fedi import FediverseInstance
from catalog.sites.goodreads import Goodreads
from catalog.sites.musicbrainz import MusicBrainzRelease
from common.models import SiteConfig

_PEER_BOOK = "https://bridge.neodb.net/book/5bP5fW4UXFdtfiCGlXQhpS"
_PEER_ALBUM = "https://bridge.neodb.net/album/2r3eBP0P9VpcvFYGW6iX9C"
_GOODREADS = "https://www.goodreads.com/book/show/11734"
_MB_RELEASE = "deeae5ff-2a7c-4f43-9d3d-6a3c3c3c3c3c"
_ISBN = "9780618260300"
_GTIN = "0601091421325"


def _payload(url: str, typ: str, title: str, linked: str) -> dict:
    return {
        "id": url,
        "type": typ,
        "localized_title": [{"lang": "en", "text": title}],
        "external_resources": [{"url": linked}],
    }


def _local(model, title: str, id_type: str, id_value: str, url: str, lookup: dict):
    item = model.objects.create(localized_title=[{"lang": "en", "text": title}])
    ExternalResource.objects.create(
        item=item,
        id_type=id_type,
        id_value=id_value,
        url=url,
        other_lookup_ids=lookup,
        metadata={"localized_title": [{"lang": "en", "text": title}]},
    )
    return item


def _fetch_peer(url: str, payload: dict):
    with (
        patch.object(FediverseInstance, "get_json_from_url", return_value=payload),
        patch.object(SiteManager, "get_redirected_url", side_effect=lambda u, **k: u),
    ):
        return FediverseInstance(url).get_resource_ready(auto_link=False)


@pytest.mark.django_db(databases="__all__")
class TestPeerLinkedResources:
    def test_book_joins_local_item_through_linked_isbn(self):
        local = _local(
            Edition,
            "The Hobbit",
            IdType.DoubanBook,
            "1046209",
            "https://book.douban.com/subject/1046209/",
            {IdType.ISBN: _ISBN},
        )
        content = ResourceContent(
            metadata={"localized_title": [{"lang": "en", "text": "The Hobbit"}]},
            lookup_ids={IdType.ISBN: _ISBN},
        )
        with patch.object(Goodreads, "scrape", return_value=content):
            res = _fetch_peer(
                _PEER_BOOK, _payload(_PEER_BOOK, "Edition", "The Hobbit", _GOODREADS)
            )
        assert res is not None
        assert res.item == local
        assert Edition.objects.count() == 1
        gr = ExternalResource.objects.get(id_type=IdType.Goodreads)
        assert gr.item == local
        # ids from the linked fetch are not copied onto the peer resource
        assert IdType.ISBN not in res.other_lookup_ids

    def test_album_joins_local_item_through_linked_gtin(self):
        local = _local(
            Album,
            "Language & Perspective",
            IdType.DoubanMusic,
            "25908955",
            "https://music.douban.com/subject/25908955/",
            {IdType.GTIN: _GTIN},
        )
        content = ResourceContent(
            metadata={
                "localized_title": [{"lang": "en", "text": "Language & Perspective"}]
            },
            lookup_ids={IdType.GTIN: _GTIN},
        )
        mb_url = f"https://musicbrainz.org/release/{_MB_RELEASE}"
        with patch.object(MusicBrainzRelease, "scrape", return_value=content):
            res = _fetch_peer(
                _PEER_ALBUM,
                _payload(_PEER_ALBUM, "Album", "Language & Perspective", mb_url),
            )
        assert res is not None
        assert res.item == local
        assert Album.objects.count() == 1

    def test_existing_linked_resource_is_used_without_fetch(self):
        local = _local(
            Edition,
            "The Hobbit",
            IdType.Goodreads,
            "11734",
            _GOODREADS,
            {IdType.ISBN: _ISBN},
        )
        with patch.object(Goodreads, "scrape") as scrape:
            res = _fetch_peer(
                _PEER_BOOK, _payload(_PEER_BOOK, "Edition", "The Hobbit", _GOODREADS)
            )
        scrape.assert_not_called()
        assert res is not None
        assert res.item == local

    def test_linked_fetch_timeout_falls_back_to_copy(self, caplog):
        local = _local(
            Edition,
            "The Hobbit",
            IdType.DoubanBook,
            "1046209",
            "https://book.douban.com/subject/1046209/",
            {IdType.ISBN: _ISBN},
        )
        downloader = MagicMock(
            url=_GOODREADS, logs=[], response_type=RESPONSE_NETWORK_ERROR
        )
        with (
            patch.object(Goodreads, "scrape", side_effect=DownloadError(downloader)),
            patch("catalog.sites.fedi.sentry_count") as count,
        ):
            res = _fetch_peer(
                _PEER_BOOK, _payload(_PEER_BOOK, "Edition", "The Hobbit", _GOODREADS)
            )
        assert res is not None
        assert res.item is not None
        assert res.item != local
        assert Edition.objects.count() == 2
        count.assert_called_once_with(
            "catalog.fedi.linked_fallback",
            attributes={"site": str(IdType.Goodreads), "reason": "download"},
        )
        assert "unable to resolve linked" in caplog.text

    def test_refetch_of_linked_peer_item_does_not_resolve_again(self):
        content = ResourceContent(
            metadata={"localized_title": [{"lang": "en", "text": "The Hobbit"}]},
            lookup_ids={IdType.ISBN: _ISBN},
        )
        payload = _payload(_PEER_BOOK, "Edition", "The Hobbit", _GOODREADS)
        with patch.object(Goodreads, "scrape", return_value=content):
            res = _fetch_peer(_PEER_BOOK, payload)
        assert res is not None and res.item is not None
        with (
            patch.object(FediverseInstance, "get_json_from_url", return_value=payload),
            patch.object(
                SiteManager, "get_redirected_url", side_effect=lambda u, **k: u
            ),
            patch.object(FediverseInstance, "resolve_linked_resources") as resolve,
        ):
            FediverseInstance(_PEER_BOOK).get_resource_ready(
                auto_link=False, ignore_existing_content=True
            )
        resolve.assert_not_called()


@pytest.mark.django_db(databases="__all__")
class TestShortRequests:
    def test_caps_timeout_and_retries(self, monkeypatch):
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        monkeypatch.setattr(SiteConfig.system, "downloader_request_timeout", 90)
        monkeypatch.setattr(SiteConfig.system, "downloader_retries", 3)
        d = BasicDownloader("https://example.org/")
        assert d.timeout == 90
        with short_requests(5):
            assert d.timeout == 5
            r = RetryDownloader("https://example.org/")
            with patch.object(
                r, "_download", return_value=(None, RESPONSE_NETWORK_ERROR)
            ) as dl:
                with pytest.raises(DownloadError):
                    r.download()
            assert dl.call_count == 1
        assert d.timeout == 90
