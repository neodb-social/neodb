from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from catalog.bookwyrm import resolve_book
from catalog.common import SiteManager
from catalog.common.downloaders import (
    RESPONSE_INVALID_CONTENT,
    RESPONSE_NETWORK_ERROR,
    DownloadError,
    set_mock_mode,
)
from catalog.common.scrapers import ParseError
from catalog.models import Edition, ExternalResource, IdType, Work
from catalog.sites.bookwyrm import BookWyrm
from catalog.sites.fedi import FediverseInstance
from takahe.models import Domain
from takahe.utils import Takahe

HOST = "bookwyrm.example"
BOOK = f"https://{HOST}/book/101"


def _peers(bookwyrm=(HOST,), blocked=()):
    return (
        patch("takahe.utils.Takahe.get_bookwyrm_peers", return_value=list(bookwyrm)),
        patch("takahe.utils.Takahe.get_blocked_peers", return_value=list(blocked)),
        patch("catalog.sites.bookwyrm.settings.SITE_DOMAINS", ["local.test"]),
    )


def _response(status: int, data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    return r


@pytest.fixture(autouse=True)
def public_hosts():
    """The fixture host does not resolve; let the URL safety check pass."""
    with patch("catalog.sites.bookwyrm.is_valid_url", return_value=True):
        yield


@pytest.fixture
def mock_http():
    """Serve test_data files, and answer any signed fetch with a 404."""
    for n in range(101, 110):
        cache.delete(f"dl:https://{HOST}/book/{n}")
    cache.delete(f"dl:https://{HOST}/author/410578")
    set_mock_mode(True)
    with (
        patch("takahe.auth.sign_get", return_value=_response(404)) as signed,
    ):
        yield signed
    set_mock_mode(False)


class TestBookWyrmUrl:
    def test_url_to_id(self):
        for url in [
            f"https://{HOST}/book/101",
            "https://BookWyrm.Example/book/101/",
            f"http://{HOST}/book/101?x=1#frag",
        ]:
            assert BookWyrm.url_to_id(url) == BOOK
        assert BookWyrm.id_to_url(BOOK) == BOOK

    def test_accepts_book_on_known_server(self):
        a, b, c = _peers()
        with a, b, c:
            assert BookWyrm.validate_url_fallback(BOOK)
            assert SiteManager.get_fallback_class_by_url(BOOK) is BookWyrm

    @pytest.mark.parametrize(
        "url",
        [
            "https://mastodon.example/book/101",
            "https://local.test/book/101",
            "https://blocked.example/book/101",
            f"https://{HOST}/author/410578",
            f"https://{HOST}/user/reader",
            f"https://{HOST}/book/101/editions",
            f"https://{HOST}:8443/book/101",
        ],
    )
    def test_rejects(self, url):
        a, b, c = _peers(
            bookwyrm=(HOST, "local.test", "blocked.example"),
            blocked=("blocked.example",),
        )
        with a, b, c:
            assert not BookWyrm.validate_url_fallback(url)

    def test_fediverse_instance_rejects_bookwyrm_host(self):
        a, b, c = _peers()
        with (
            a,
            b,
            c,
            patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            patch.object(
                FediverseInstance,
                "get_json_from_url",
                return_value={"id": BOOK, "type": "Edition", "category": "book"},
            ) as fetch,
        ):
            assert not FediverseInstance.validate_url_fallback(BOOK)
            fetch.assert_not_called()

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_fediverse_instance_requires_category(self, downloader):
        url = "https://unknown.example/book/1"
        downloader.return_value.download.return_value.json.return_value = {
            "id": url,
            "type": "Edition",
            "title": "x",
        }
        with pytest.raises(ValueError):
            FediverseInstance.get_json_from_url(url)


@pytest.mark.django_db(databases="__all__")
class TestBookWyrmPeers:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        cache.delete(Takahe.BOOKWYRM_PEERS_CACHE_KEY)
        yield
        cache.delete(Takahe.BOOKWYRM_PEERS_CACHE_KEY)

    def test_peers_from_nodeinfo(self):
        Domain.objects.create(
            domain="bw.example",
            local=False,
            nodeinfo={"software": {"name": "bookwyrm"}},
        )
        Domain.objects.create(
            domain="masto.example",
            local=False,
            nodeinfo={"software": {"name": "mastodon"}},
        )
        Domain.objects.create(
            domain="badbw.example",
            local=False,
            blocked=True,
            nodeinfo={"software": {"name": "bookwyrm"}},
        )
        assert Takahe.get_bookwyrm_peers() == ["bw.example"]

    def test_register_peers(self):
        Domain.objects.create(
            domain="masto.example",
            local=False,
            nodeinfo={"software": {"name": "mastodon"}},
        )
        Domain.objects.create(domain="bare.example", local=False, state="updated")
        Takahe.get_bookwyrm_peers()  # fill the cache
        n = Takahe.register_bookwyrm_peers(
            {
                "new.example": "0.7.5",
                "bare.example": "0.7.4",
                "masto.example": "0.7.5",
                "Not A Domain": "1",
            }
        )
        assert n == 2
        new = Domain.objects.get(pk="new.example")
        assert new.nodeinfo == {"software": {"name": "bookwyrm", "version": "0.7.5"}}
        assert new.state == "outdated"
        assert not new.local
        assert Domain.objects.get(pk="bare.example").state == "outdated"
        assert Domain.objects.get(pk="masto.example").nodeinfo == {
            "software": {"name": "mastodon"}
        }
        assert set(Takahe.get_bookwyrm_peers()) == {"new.example", "bare.example"}

    @patch("catalog.sites.bookwyrm.httpx.get")
    def test_fetch_fedidb_hosts(self, get):
        nxt = "https://api.fedidb.org/v1/servers?software=bookwyrm&cursor=abc"
        get.side_effect = [
            _response(
                200,
                {
                    "data": [
                        {
                            "domain": "BW.example",
                            "software": {"slug": "bookwyrm", "version": "0.7.5"},
                        },
                        {"domain": "m.example", "software": {"slug": "mastodon"}},
                    ],
                    "links": {"next": nxt},
                },
            ),
            _response(
                200,
                {
                    "data": [
                        {"domain": "two.example", "software": {"slug": "bookwyrm"}}
                    ],
                    "links": {"next": "https://evil.example/next"},
                },
            ),
        ]
        assert BookWyrm.fetch_fedidb_hosts() == {
            "bw.example": "0.7.5",
            "two.example": "",
        }
        assert get.call_count == 2
        assert get.call_args_list[1].args[0] == nxt


@pytest.mark.django_db(databases="__all__")
class TestBookWyrmScrape:
    def test_mapping(self, mock_http):
        with patch(
            "catalog.sites.bookwyrm.BasicImageDownloader.download_image",
            return_value=(b"img", "jpeg"),
        ) as img:
            content = BookWyrm(BOOK).scrape()
        img.assert_called_once_with(
            f"https://{HOST}/images/covers/geomagician.jpeg", None, headers={}
        )
        m = content.metadata
        assert m["title"] == "The Geomagician"
        assert m["localized_title"] == [{"lang": "en", "text": "The Geomagician"}]
        assert m["subtitle"] == "A Novel"
        # the other-host author is not fetched and the missing one is skipped
        assert m["author"] == ["Jennifer Mandula"]
        assert m["language"] == ["en"]
        assert (m["pub_year"], m["pub_month"]) == (2026, 3)
        assert m["publisher"] == ["Del Rey"]
        assert m["pages"] == 452
        assert m["format"] == Edition.BookFormat.EBOOK
        assert m["series"] == "Fossil Magic"
        assert "geomagician" in m["localized_description"][0]["text"]
        assert "<" not in m["localized_description"][0]["text"]
        assert content.cover_image == b"img"
        assert content.lookup_ids == {
            IdType.ISBN: "9780593983317",
            IdType.OpenLibrary: "OL60000001M",
            IdType.Goodreads: "220000001",
            IdType.OCLC: "1234567",
        }

    def test_isbn10_only(self, mock_http):
        content = BookWyrm(f"https://{HOST}/book/102").scrape()
        assert content.lookup_ids == {IdType.ISBN: "9780306406157"}
        assert content.metadata["isbn"] == "9780306406157"
        assert (content.metadata["pub_year"], content.metadata["pub_month"]) == (
            1999,
            None,
        )

    def test_id_mismatch_rejected(self, mock_http):
        with pytest.raises(ParseError):
            BookWyrm(f"https://{HOST}/book/105").scrape()
        assert resolve_book(f"https://{HOST}/book/105", HOST) is None

    def test_work(self, mock_http):
        content = BookWyrm(f"https://{HOST}/book/106").scrape()
        assert content.metadata["preferred_model"] == "Work"
        assert content.metadata["author"] == ["Jennifer Mandula"]
        assert content.lookup_ids == {IdType.OpenLibrary_Work: "OL45883W"}
        assert "pub_year" not in content.metadata
        # reviews attach to editions only
        assert resolve_book(f"https://{HOST}/book/106", HOST) is None
        res = ExternalResource.objects.get(id_value=f"https://{HOST}/book/106")
        assert isinstance(res.item, Work)

    def test_edition_names_its_work(self, mock_http):
        content = BookWyrm(BOOK).scrape()
        assert content.metadata["required_resources"] == [
            {
                "model": "Work",
                "id_type": IdType.BookWyrm,
                "id_value": f"https://{HOST}/book/1019",
                "title": "The Geomagician",
            }
        ]

    def test_signed_fetch_after_refusal(self):
        cache.delete(f"dl:{BOOK}")
        refused = MagicMock(url=BOOK, logs=[], response_type=RESPONSE_INVALID_CONTENT)
        data = {"id": BOOK, "type": "Edition", "title": "Signed"}
        with (
            patch("catalog.sites.bookwyrm.CachedDownloader") as downloader,
            patch("takahe.auth.sign_get", return_value=_response(200, data)) as signed,
        ):
            downloader.return_value.download.side_effect = DownloadError(refused)
            assert BookWyrm(BOOK).fetch_book() == data
        signed.assert_called_once_with(BOOK, timeout=BookWyrm.FETCH_TIMEOUT)

    def test_no_signed_fetch_after_timeout(self):
        timeout = MagicMock(url=BOOK, logs=[], response_type=RESPONSE_NETWORK_ERROR)
        with (
            patch("catalog.sites.bookwyrm.CachedDownloader") as downloader,
            patch("takahe.auth.sign_get") as signed,
        ):
            downloader.return_value.download.side_effect = DownloadError(timeout)
            with pytest.raises(DownloadError):
                BookWyrm(BOOK).fetch_book()
        signed.assert_not_called()

    def test_signed_fetch_refused_too(self):
        refused = MagicMock(url=BOOK, logs=[], response_type=RESPONSE_INVALID_CONTENT)
        with (
            patch("catalog.sites.bookwyrm.CachedDownloader") as downloader,
            patch("takahe.auth.sign_get", return_value=_response(403)),
        ):
            downloader.return_value.download.side_effect = DownloadError(refused)
            with pytest.raises(DownloadError):
                BookWyrm(BOOK).fetch_book()


@pytest.mark.django_db(databases="__all__")
class TestResolveBook:
    def test_isbn_match_links_existing_item(self, mock_http):
        existing = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "The Geomagician"}]
        )
        existing.isbn = "9780593983317"
        existing.save()
        assert resolve_book(BOOK, HOST) == existing
        res = ExternalResource.objects.get(id_type=IdType.BookWyrm, id_value=BOOK)
        assert res.item_id == existing.pk
        assert not ExternalResource.objects.filter(id_type=IdType.Fediverse).exists()

    def test_edition_linked_to_work(self, mock_http):
        edition = resolve_book(f"https://{HOST}/book/108", HOST)
        assert isinstance(edition, Edition)
        work = edition.get_work()
        assert isinstance(work, Work)
        assert work.display_title == "The Geomagician"

    def test_no_ids_creates_edition(self, mock_http):
        item = resolve_book(f"https://{HOST}/book/103", HOST)
        assert isinstance(item, Edition)
        assert item.display_title == "Small Press Zine No. 7"
        assert item.pub_year == 2026 and item.pub_month == 11
        assert not ExternalResource.objects.filter(id_type=IdType.Fediverse).exists()

    def test_openlibrary_resource_match(self, mock_http):
        existing = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Fantastic Mr Fox"}]
        )
        ExternalResource.objects.create(
            item=existing,
            id_type=IdType.OpenLibrary,
            id_value="OL7353617M",
            url="https://openlibrary.org/books/OL7353617M",
            metadata={"localized_title": [{"lang": "en", "text": "Fantastic Mr Fox"}]},
        )
        assert resolve_book(f"https://{HOST}/book/104", HOST) == existing

    def test_known_server_without_author_host(self, mock_http):
        a, b, c = _peers()
        with a, b, c, patch("catalog.common.sites.is_valid_url", return_value=True):
            item = resolve_book(f"https://{HOST}/book/103", "reader.example")
        assert isinstance(item, Edition)

    def test_unknown_server_without_author_host(self, mock_http):
        a, b, c = _peers(bookwyrm=())
        with a, b, c, patch("catalog.common.sites.is_valid_url", return_value=True):
            assert resolve_book(f"https://{HOST}/book/103", "reader.example") is None
        assert not ExternalResource.objects.filter(id_value__contains=HOST).exists()

    def test_blocked_server(self, mock_http):
        a, b, c = _peers(blocked=(HOST,))
        with a, b, c:
            assert resolve_book(f"https://{HOST}/book/103", HOST) is None
