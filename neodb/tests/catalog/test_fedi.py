"""
Tests for the FediverseInstance site implementation
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.core.cache import cache

from catalog.common import ResourceContent
from catalog.common.downloaders import DownloadError, use_local_response
from catalog.models import Album, ExternalResource, IdType, ItemCategory
from catalog.sites.fedi import FediverseInstance
from common.models import SiteConfig
from takahe.models import Domain
from takahe.utils import Takahe


class TestFediverseInstance:
    """Test cases for FediverseInstance"""

    def test_url_to_id(self):
        """Test URL to ID conversion"""
        test_cases = [
            (
                "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
                "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
            ),
            ("https://example.com/api/movie/123", "https://example.com/movie/123"),
            (
                "https://EXAMPLE.COM/book/456?param=value",
                "https://example.com/book/456",
            ),
        ]

        for url, expected_id in test_cases:
            assert FediverseInstance.url_to_id(url) == expected_id

    def test_id_to_url(self):
        """Test ID to URL conversion"""
        test_id = "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"
        assert FediverseInstance.id_to_url(test_id) == test_id

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_json_from_url_valid(self, mock_downloader):
        """Test getting valid JSON from URL"""
        # Mock response data
        mock_json_data = {
            "id": "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
            "type": "Movie",
            "title": "Billy Lynn's Long Halftime Walk",
            "description": "Test description",
            "external_resources": [],
        }

        # Configure mock
        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        result = FediverseInstance.get_json_from_url(
            "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"
        )

        assert result == mock_json_data
        mock_downloader.assert_called_once()

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_json_from_url_invalid_type(self, mock_downloader):
        """Test getting JSON with invalid type"""
        mock_json_data = {
            "id": "https://eggplant.place/item/123",
            "type": "InvalidType",
            "title": "Test Item",
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        with pytest.raises(ValueError, match="Not a supported format or type"):
            FediverseInstance.get_json_from_url("https://eggplant.place/item/123")

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_json_from_url_id_mismatch(self, mock_downloader):
        """Test getting JSON with ID mismatch"""
        mock_json_data = {
            "id": "https://different.place/movie/123",
            "type": "Movie",
            "title": "Test Movie",
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        with pytest.raises(ValueError, match="ID mismatch"):
            FediverseInstance.get_json_from_url(
                "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"
            )

    @pytest.mark.django_db(databases="__all__")
    @use_local_response
    def test_scrape_movie(self):
        site = FediverseInstance("https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4")
        content = site.scrape()
        assert isinstance(content, ResourceContent)
        assert content.metadata is not None
        # Check basic metadata
        metadata = content.metadata
        assert metadata["type"] == "Movie"
        assert metadata["title"] == "Billy Lynn's Long Halftime Walk"
        assert metadata["preferred_model"] == "Movie"
        assert IdType.IMDB in content.lookup_ids
        assert IdType.DoubanMovie in content.lookup_ids
        assert content.lookup_ids[IdType.IMDB] == "tt2513074"
        assert content.lookup_ids[IdType.DoubanMovie] == "25983044"

    def test_is_local_item_url(self):
        """Test local item URL detection"""
        with patch(
            "catalog.sites.fedi.settings.SITE_DOMAINS", ["neodb.social", "local.test"]
        ):
            assert (
                FediverseInstance.is_local_item_url("https://neodb.social/movie/123")
                is True
            )
            assert (
                FediverseInstance.is_local_item_url("https://local.test/book/456")
                is True
            )
            assert (
                FediverseInstance.is_local_item_url("https://external.com/movie/789")
                is False
            )
            # a hostname-less or malformed URL is not local, and must not raise
            assert FediverseInstance.is_local_item_url("neodb.social") is False
            assert FediverseInstance.is_local_item_url("") is False
            # userinfo must not be mistaken for the host
            assert (
                FediverseInstance.is_local_item_url(
                    "https://neodb.social@external.com/movie/789"
                )
                is False
            )
            assert (
                FediverseInstance.is_local_item_url(
                    "https://external.com@neodb.social/movie/789"
                )
                is True
            )

    def test_is_local_item_url_with_port(self):
        """A SITE_DOMAINS entry may carry a port in local setups"""
        with patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["localhost:8000"]):
            assert (
                FediverseInstance.is_local_item_url("http://localhost:8000/movie/123")
                is True
            )
            assert (
                FediverseInstance.is_local_item_url("http://localhost/movie/123")
                is False
            )

    @patch("catalog.sites.fedi.CachedDownloader")
    @patch("catalog.sites.fedi.Item.get_by_url")
    def test_get_local_item_from_external_resources(
        self, mock_get_by_url, mock_downloader
    ):
        """Test getting local item from external resources"""
        mock_json_data = {
            "id": "https://external.place/movie/123",
            "type": "Movie",
            "external_resources": [
                {"url": "https://neodb.social/movie/456"},
                {"url": "https://external.com/movie/789"},
            ],
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        # Mock local item
        mock_item = type("MockItem", (), {"is_deleted": False})()
        mock_get_by_url.return_value = mock_item

        site = FediverseInstance("https://external.place/movie/123")

        with patch.object(site, "is_local_item_url") as mock_is_local:
            mock_is_local.side_effect = lambda url: "neodb.social" in url

            result = site.get_local_item_from_external_resources()

            assert result == mock_item
            mock_get_by_url.assert_called_once_with(
                "https://neodb.social/movie/456", True
            )

    def test_peer_search_task(self):
        """Test peer search functionality"""

        # Create a proper async mock response
        async def async_get(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {
                        "url": "/movie/123",
                        "category": "movie",
                        "display_title": "Test Movie",
                        "brief": "A test movie",
                        "cover_image_url": "https://example.com/image.jpg",
                        "external_resources": [],
                    }
                ]
            }
            return mock_response

        async def run_test():
            with patch("httpx.AsyncClient") as mock_client_class:
                # Create async context manager mock
                mock_client = AsyncMock()
                mock_client.get = async_get
                mock_client_class.return_value.__aenter__.return_value = mock_client

                results = await FediverseInstance.peer_search_task(
                    "example.com", "test query", 1
                )

                assert len(results) == 1
                assert results[0].source_url == "https://example.com/movie/123"
                assert results[0].display_title == "Test Movie"

        # Run the async test
        asyncio.run(run_test())

    def test_validate_url_fallback_success(self):
        """Test URL validation when get_json_from_url succeeds"""
        with (
            patch("catalog.sites.fedi.URLValidator") as mock_validator,
            patch(
                "catalog.sites.fedi.FediverseInstance.get_json_from_url"
            ) as mock_get_json,
            patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            patch("takahe.utils.Takahe.get_blocked_peers", return_value=[]),
            patch(
                "takahe.utils.Takahe.get_neodb_peers",
                return_value=["peer1.com", "peer2.com"],
            ),
        ):
            mock_validator.return_value.return_value = None  # Valid URL
            mock_get_json.return_value = {
                "type": "Movie",
                "id": "https://example.com/movie/123",
            }

            result = FediverseInstance.validate_url_fallback(
                "https://example.com/movie/123"
            )
            assert result is True

    def test_validate_url_fallback_local_domain(self):
        """Test URL validation rejects local domain URLs"""
        with (
            patch("catalog.sites.fedi.URLValidator") as mock_validator,
            patch(
                "catalog.sites.fedi.settings.SITE_DOMAINS",
                ["local.test", "example.com"],
            ),
        ):
            mock_validator.return_value.return_value = None  # Valid URL

            result = FediverseInstance.validate_url_fallback(
                "https://example.com/movie/123"
            )
            assert result is False

    def test_validate_url_fallback_blocked_peer(self):
        """Test URL validation rejects blocked peer URLs"""
        with (
            patch("catalog.sites.fedi.URLValidator") as mock_validator,
            patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            patch(
                "takahe.utils.Takahe.get_blocked_peers", return_value=["blocked.com"]
            ),
        ):
            mock_validator.return_value.return_value = None  # Valid URL

            result = FediverseInstance.validate_url_fallback(
                "https://blocked.com/movie/123"
            )
            assert result is False

    def test_validate_url_fallback_download_error(self):
        """Test URL validation handles download errors"""

        with (
            patch("catalog.sites.fedi.URLValidator") as mock_validator,
            patch(
                "catalog.sites.fedi.FediverseInstance.get_json_from_url"
            ) as mock_get_json,
            patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            patch("takahe.utils.Takahe.get_blocked_peers", return_value=[]),
            patch("takahe.utils.Takahe.get_neodb_peers", return_value=["peer1.com"]),
        ):
            mock_validator.return_value.return_value = None  # Valid URL

            # Create a mock downloader object for DownloadError
            mock_downloader = MagicMock()
            mock_downloader.url = "https://peer1.com/movie/123"
            mock_downloader.logs = []
            mock_downloader.response_type = 1  # RESPONSE_NETWORK_ERROR

            mock_get_json.side_effect = DownloadError(mock_downloader, "Network error")

            result = FediverseInstance.validate_url_fallback(
                "https://peer1.com/movie/123"
            )
            assert result is False

    def test_validate_url_fallback_general_exception(self):
        """Test URL validation handles general exceptions"""
        with (
            patch("catalog.sites.fedi.URLValidator") as mock_validator,
            patch(
                "catalog.sites.fedi.FediverseInstance.get_json_from_url"
            ) as mock_get_json,
            patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            patch("takahe.utils.Takahe.get_blocked_peers", return_value=[]),
            patch("takahe.utils.Takahe.get_neodb_peers", return_value=["peer1.com"]),
        ):
            mock_validator.return_value.return_value = None  # Valid URL
            mock_get_json.side_effect = ValueError("Invalid JSON")

            result = FediverseInstance.validate_url_fallback(
                "https://peer1.com/movie/123"
            )
            assert result is False

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_json_from_url_network_error(self, mock_downloader):
        """Test get_json_from_url handles network errors"""

        # Create a mock downloader object for DownloadError
        mock_downloader_obj = MagicMock()
        mock_downloader_obj.url = "https://example.com/movie/123"
        mock_downloader_obj.logs = []
        mock_downloader_obj.response_type = 1  # RESPONSE_NETWORK_ERROR

        mock_instance = mock_downloader.return_value
        mock_instance.download.side_effect = DownloadError(
            mock_downloader_obj, "Network timeout"
        )

        with pytest.raises(DownloadError):
            FediverseInstance.get_json_from_url("https://example.com/movie/123")

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_json_from_url_invalid_json(self, mock_downloader):
        """Test get_json_from_url handles invalid JSON"""
        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = "not a dict"

        with pytest.raises(ValueError, match="Not a supported format or type"):
            FediverseInstance.get_json_from_url("https://example.com/movie/123")

    def test_peer_search_task_network_error(self):
        """Test peer search task handles network errors"""

        async def async_get_error(*args, **kwargs):
            raise Exception("Network error")

        async def run_test():
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = async_get_error
                mock_client_class.return_value.__aenter__.return_value = mock_client

                results = await FediverseInstance.peer_search_task(
                    "example.com", "test query", 1
                )

                assert results == []

        asyncio.run(run_test())

    def test_peer_search_task_no_data(self):
        """Test peer search task handles response without data"""

        async def async_get_no_data(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.json.return_value = {"message": "No results found"}
            return mock_response

        async def run_test():
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = async_get_no_data
                mock_client_class.return_value.__aenter__.return_value = mock_client

                results = await FediverseInstance.peer_search_task(
                    "example.com", "test query", 1
                )

                assert results == []

        asyncio.run(run_test())

    def test_peer_search_task_with_local_resources(self):
        """Test peer search filters out items with local resources"""

        async def async_get_with_local(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {
                        "url": "/movie/123",
                        "category": "movie",
                        "display_title": "Test Movie",
                        "brief": "A test movie",
                        "cover_image_url": "https://example.com/image.jpg",
                        "external_resources": [{"url": "https://local.test/movie/456"}],
                    },
                    {
                        "url": "/movie/789",
                        "category": "movie",
                        "display_title": "Another Movie",
                        "brief": "Another test movie",
                        "cover_image_url": "https://example.com/image2.jpg",
                        "external_resources": [],
                    },
                ]
            }
            return mock_response

        async def run_test():
            with (
                patch("httpx.AsyncClient") as mock_client_class,
                patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]),
            ):
                mock_client = AsyncMock()
                mock_client.get = async_get_with_local
                mock_client_class.return_value.__aenter__.return_value = mock_client

                results = await FediverseInstance.peer_search_task(
                    "example.com", "test query", 1
                )

                # Should only return the second item (without local resources)
                assert len(results) == 1
                assert results[0].display_title == "Another Movie"

        asyncio.run(run_test())

    def test_get_peers_for_search_disabled(self):
        """Test get_peers_for_search when disabled"""
        with patch.object(SiteConfig.system, "search_peers", ["-"]):
            result = FediverseInstance.get_peers_for_search()
            assert result == []

    def test_get_peers_for_search_custom_peers(self):
        """Test get_peers_for_search with custom peers"""
        with patch.object(
            SiteConfig.system, "search_peers", ["peer1.com", "peer2.com"]
        ):
            result = FediverseInstance.get_peers_for_search()
            assert result == ["peer1.com", "peer2.com"]

    def test_get_peers_for_search_from_takahe(self):
        """Test get_peers_for_search from Takahe"""
        with (
            patch.object(SiteConfig.system, "search_peers", []),
            patch(
                "takahe.utils.Takahe.get_neodb_peers",
                return_value=["takahe1.com", "takahe2.com"],
            ),
        ):
            result = FediverseInstance.get_peers_for_search()
            assert result == ["takahe1.com", "takahe2.com"]

    def test_peer_search_endpoint(self):
        default = "https://peer.com/api/catalog/search"
        assert FediverseInstance.peer_search_endpoint("peer.com", None) == default
        for good in [
            "https://peer.com/api/v2/search",
            "https://search.peer.com/q?x=1",
            "https://PEER.com:8443/s",
        ]:
            assert FediverseInstance.peer_search_endpoint("peer.com", good) == good
        for bad in [
            "http://peer.com/api/catalog/search",
            "https://victim.com/api/catalog/search",
            "https://notpeer.com/s",
            "https://peer.com.evil.com/s",
            "https://peer.com/s#frag",
            "https://user@peer.com/s",
            "https://[peer.com/search",
            "/api/catalog/search",
            "",
        ]:
            assert FediverseInstance.peer_search_endpoint("peer.com", bad) == default

    def test_peer_search_result_tolerates_partial_items(self):
        def result(item):
            r = FediverseInstance.peer_search_result("peer.com", item)
            return r and (r.source_url, r.display_title, r.category)

        with patch("catalog.sites.fedi.settings.SITE_DOMAINS", ["local.test"]):
            assert result({"url": "/book/1", "display_title": "A"}) == (
                "https://peer.com/book/1",
                "A",
                None,
            )
            assert result(
                {
                    "url": "https://other.peer.com/movie/2",
                    "display_title": "B",
                    "category": "movie",
                    "brief": None,
                    "cover_image_url": 5,
                    "external_resources": "x",
                }
            ) == ("https://other.peer.com/movie/2", "B", ItemCategory.Movie)
            for bad in [
                None,
                "x",
                {"url": "/book/1"},
                {"display_title": "A"},
                {"url": 1, "display_title": "A"},
                {"url": "/book/1", "display_title": ["A"]},
                {"url": "javascript:alert(1)", "display_title": "A"},
                {"url": "http://peer.com/book/1", "display_title": "A"},
                {"url": "https://evil.com/book/1", "display_title": "A"},
                {"url": "//evil.com/book/1", "display_title": "A"},
                {"url": " https://evil.com/book/1", "display_title": "A"},
                {"url": "https://[peer.com/book/1", "display_title": "A"},
                {"url": "/book/1", "display_title": "A", "external_resources": 1},
                {
                    "url": "/book/1",
                    "display_title": "A",
                    "external_resources": [{"url": "https://local.test/book/9"}],
                },
            ]:
                assert result(bad) is None, bad

    @pytest.mark.parametrize(
        "payload,urls",
        [
            ([], []),
            ("x", []),
            ({"data": None}, []),
            ({"data": {"url": "/book/1"}}, []),
            (
                {"data": [None, {"url": "/book/1", "display_title": "A"}]},
                ["https://peer.com/book/1"],
            ),
        ],
    )
    def test_peer_search_task_malformed_response(self, payload, urls):
        async def async_get(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.json.return_value = payload
            return mock_response

        async def run_test():
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = async_get
                mock_client_class.return_value.__aenter__.return_value = mock_client
                return await FediverseInstance.peer_search_task("peer.com", "q", 1)

        results = asyncio.run(run_test())
        assert [r.source_url for r in results] == urls

    def test_peer_search_task_uses_endpoint(self):
        calls = []

        async def async_get(url, **kwargs):
            calls.append((url, kwargs.get("headers", {}).get("User-Agent", "")))
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": []}
            return mock_response

        async def run_test():
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = async_get
                mock_client_class.return_value.__aenter__.return_value = mock_client
                await FediverseInstance.peer_search_task(
                    "peer.com", "a b", 1, "book", 5, "https://s.peer.com/q?v=2"
                )
                await FediverseInstance.peer_search_task(
                    "peer.com", "a b", 1, None, 5, "https://evil.com/q"
                )

        asyncio.run(run_test())
        assert [c[0] for c in calls] == [
            "https://s.peer.com/q?v=2&query=a+b&page=1&category=book",
            "https://peer.com/api/catalog/search?query=a+b&page=1",
        ]
        assert all(ua.startswith("NeoDB/") for _, ua in calls)

    @pytest.mark.parametrize("search_peers", [[], ["peer.com", "off.com", "old.com"]])
    def test_search_tasks_honor_peer_settings(self, search_peers):
        with (
            patch.object(SiteConfig.system, "search_peers", search_peers),
            patch(
                "takahe.utils.Takahe.get_neodb_peers",
                return_value=["peer.com", "off.com", "old.com"],
            ),
            patch(
                "takahe.utils.Takahe.get_neodb_search_settings",
                return_value=({"off.com"}, {"peer.com": "https://peer.com/s"}),
            ),
            patch.object(
                FediverseInstance, "peer_search_task", new_callable=MagicMock
            ) as mock_task,
        ):
            FediverseInstance.search_tasks("q", 1, "all", 5)
        assert [c.args for c in mock_task.call_args_list] == [
            ("peer.com", "q", 1, "all", 5, "https://peer.com/s"),
            ("old.com", "q", 1, "all", 5, None),
        ]

    @pytest.mark.django_db(databases="__all__")
    def test_get_neodb_search_settings(self):
        def add_peer(domain: str, **metadata):
            Domain.objects.create(
                domain=domain,
                local=False,
                state="updated",
                nodeinfo={
                    "software": {"name": "other", "version": "1"},
                    "protocols": ["activitypub", "neodb"],
                    "metadata": {"nodeEnvironment": "production"} | metadata,
                },
            )

        add_peer(
            "on.example.com",
            neodbFeatures=["catalog.search", "catalog.item"],
            neodbFeatureParams={
                "catalog.search": {"endpoint": "https://on.example.com/s"}
            },
        )
        add_peer("plain.example.com", neodbFeatures=["catalog.search"])
        add_peer("off.example.com", neodbFeatures=[])
        add_peer("items.example.com", neodbFeatures=["catalog.item"])
        add_peer("legacy.example.com")
        add_peer("odd.example.com", neodbFeatures="catalog.search")
        add_peer(
            "badparams.example.com",
            neodbFeatures=["catalog.search"],
            neodbFeatureParams={"catalog.search": {"endpoint": 123}},
        )
        cache.delete("neodb_search_settings")
        try:
            assert Takahe.get_neodb_search_settings() == (
                {"off.example.com", "items.example.com"},
                {"on.example.com": "https://on.example.com/s"},
            )
        finally:
            cache.delete("neodb_search_settings")

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_local_item_from_external_resources_no_json(self, mock_downloader):
        """Test get_local_item_from_external_resources when JSON fetch fails"""
        mock_instance = mock_downloader.return_value
        mock_instance.download.side_effect = ValueError("Network error")

        site = FediverseInstance("https://external.place/movie/123")
        result = site.get_local_item_from_external_resources()

        assert result is None

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_local_item_from_external_resources_invalid_data(self, mock_downloader):
        """Test get_local_item_from_external_resources with invalid data"""
        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = "not a dict"

        site = FediverseInstance("https://external.place/movie/123")
        result = site.get_local_item_from_external_resources()

        assert result is None

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    @patch("catalog.sites.fedi.Item.get_by_url")
    def test_get_local_item_from_external_resources_deleted_item(
        self, mock_get_by_url, mock_downloader
    ):
        """Test get_local_item_from_external_resources with deleted item"""
        mock_json_data = {
            "id": "https://external.place/movie/123",
            "type": "Movie",
            "external_resources": [{"url": "https://neodb.social/movie/456"}],
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        # Mock deleted item
        mock_item = type("MockItem", (), {"is_deleted": True})()
        mock_get_by_url.return_value = mock_item

        site = FediverseInstance("https://external.place/movie/123")

        with patch.object(site, "is_local_item_url", return_value=True):
            result = site.get_local_item_from_external_resources()
            assert result is None

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    @patch("catalog.sites.fedi.BasicImageDownloader.download_image")
    @patch("catalog.sites.fedi.SiteManager.get_site_by_url")
    def test_scrape_with_fediverse_external_resource(
        self, mock_site_manager, mock_image_downloader, mock_downloader
    ):
        """Test scraping with Fediverse external resource (should be skipped)"""
        mock_json_data = {
            "id": "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
            "type": "Movie",
            "title": "Test Movie",
            "external_resources": [{"url": "https://another.fediverse/movie/123"}],
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data
        mock_image_downloader.return_value = (None, None)

        # Mock site manager to return a Fediverse site
        mock_fedi_site = type(
            "MockSite",
            (),
            {
                "ID_TYPE": IdType.Fediverse,
                "id_value": "123",
                "url": "https://another.fediverse/movie/123",
            },
        )()
        mock_site_manager.return_value = mock_fedi_site

        site = FediverseInstance("https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4")
        content = site.scrape()

        # Should not include the Fediverse external resource in lookup_ids
        assert IdType.Fediverse not in content.lookup_ids

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    @patch("catalog.sites.fedi.BasicImageDownloader.download_image")
    @patch("catalog.sites.fedi.SiteManager.get_site_by_url")
    def test_scrape_with_incompatible_external_resource(
        self, mock_site_manager, mock_image_downloader, mock_downloader
    ):
        """Test scraping with incompatible external resource"""
        mock_json_data = {
            "id": "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
            "type": "Movie",
            "title": "Test Movie",
            "external_resources": [
                {"url": "https://example.com/book/123"}  # Book resource for Movie
            ],
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data
        mock_image_downloader.return_value = (None, None)

        # Mock site manager to return an incompatible site
        mock_book_site = type(
            "MockSite",
            (),
            {
                "ID_TYPE": IdType.ISBN,
                "id_value": "123",
                "url": "https://example.com/book/123",
                "check_model_compatibility": lambda self, model_cls: (
                    False
                ),  # Incompatible
            },
        )()
        mock_site_manager.return_value = mock_book_site

        site = FediverseInstance("https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4")
        content = site.scrape()

        # Should not include the incompatible external resource in lookup_ids
        assert IdType.ISBN not in content.lookup_ids


_BRIDGE_URL = (
    "https://bridge.neodb.net/catalog/music/"
    "mbReleaseId-6e30c2f9-355f-410e-bde6-a578c011203e"
)
_MB_RELEASE_ID = "6e30c2f9-355f-410e-bde6-a578c011203e"


class TestFediversePrimaryLookupId:
    """An imported item must be identified by a real site id when it knows one"""

    @staticmethod
    def _album(other_lookup_ids: dict) -> Album:
        res = ExternalResource(
            id_type=IdType.Fediverse,
            id_value=_BRIDGE_URL,
            url=_BRIDGE_URL,
            other_lookup_ids=other_lookup_ids,
        )
        album = Album()
        album._update_primary_lookup_id([res])
        return album

    def test_musicbrainz_release_is_an_ideal_id(self):
        album = self._album({IdType.MusicBrainz_Release: _MB_RELEASE_ID})
        assert album.primary_lookup_id_type == IdType.MusicBrainz_Release
        assert album.primary_lookup_id_value == _MB_RELEASE_ID

    def test_non_ideal_site_id_beats_the_peer_url(self):
        album = self._album({IdType.AppleMusic: "123456"})
        assert album.primary_lookup_id_type == IdType.AppleMusic
        assert album.primary_lookup_id_value == "123456"

    def test_ideal_id_beats_a_non_ideal_site_id(self):
        album = self._album(
            {IdType.AppleMusic: "123456", IdType.MusicBrainz_Release: _MB_RELEASE_ID}
        )
        assert album.primary_lookup_id_type == IdType.MusicBrainz_Release

    def test_peer_url_remains_when_nothing_else_is_known(self):
        album = self._album({})
        assert album.primary_lookup_id_type == IdType.Fediverse
        assert album.primary_lookup_id_value == _BRIDGE_URL

    def test_album_edit_form_offers_musicbrainz_ids(self):
        choices = dict(Album.lookup_id_type_choices())
        assert IdType.MusicBrainz_Release in choices
        assert IdType.MusicBrainz_ReleaseGroup in choices
        assert IdType.AppleMusic in choices

    @pytest.mark.django_db(databases="__all__")
    @use_local_response
    def test_scrape_bridged_album(self):
        site = FediverseInstance(_BRIDGE_URL)
        content = site.scrape()
        assert isinstance(content, ResourceContent)
        assert content.metadata["preferred_model"] == "Album"
        assert content.lookup_ids[IdType.MusicBrainz_Release] == _MB_RELEASE_ID

    @pytest.mark.django_db(databases="__all__")
    @use_local_response
    def test_bridged_album_item_uses_musicbrainz_id(self):
        site = FediverseInstance(_BRIDGE_URL)
        resource = site.get_resource_ready(auto_link=False)
        assert resource is not None
        assert resource.item is not None
        assert resource.item.primary_lookup_id_type == IdType.MusicBrainz_Release
        assert resource.item.primary_lookup_id_value == _MB_RELEASE_ID
