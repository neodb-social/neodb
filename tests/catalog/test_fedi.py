"""
Tests for the FediverseInstance site implementation
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catalog.common import ResourceContent
from catalog.common.downloaders import use_local_response
from catalog.models import IdType
from catalog.sites.fedi import FediverseInstance


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
        from catalog.common.downloaders import DownloadError

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
        from catalog.common.downloaders import DownloadError

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
        with patch("catalog.sites.fedi.settings.SEARCH_PEERS", ["-"]):
            result = FediverseInstance.get_peers_for_search()
            assert result == []

    def test_get_peers_for_search_custom_peers(self):
        """Test get_peers_for_search with custom peers"""
        with patch(
            "catalog.sites.fedi.settings.SEARCH_PEERS", ["peer1.com", "peer2.com"]
        ):
            result = FediverseInstance.get_peers_for_search()
            assert result == ["peer1.com", "peer2.com"]

    def test_get_peers_for_search_from_takahe(self):
        """Test get_peers_for_search from Takahe"""
        with (
            patch("catalog.sites.fedi.settings.SEARCH_PEERS", None),
            patch(
                "takahe.utils.Takahe.get_neodb_peers",
                return_value=["takahe1.com", "takahe2.com"],
            ),
        ):
            result = FediverseInstance.get_peers_for_search()
            assert result == ["takahe1.com", "takahe2.com"]

    def test_search_tasks_category_conversion(self):
        """Test search_tasks converts movietv category correctly"""
        with patch.object(
            FediverseInstance,
            "get_peers_for_search",
            return_value=["peer1.com", "peer2.com"],
        ):
            tasks = FediverseInstance.search_tasks("test query", 1, "movietv", 5)

            # Should have 2 tasks (one for each peer)
            assert len(tasks) == 2

    @pytest.mark.django_db(databases="__all__")
    @patch("catalog.sites.fedi.CachedDownloader")
    def test_get_local_item_from_external_resources_no_json(self, mock_downloader):
        """Test get_local_item_from_external_resources when JSON fetch fails"""
        mock_instance = mock_downloader.return_value
        mock_instance.download.side_effect = Exception("Network error")

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

    @patch("catalog.sites.fedi.CachedDownloader")
    def test_scrape_parse_error(self, mock_downloader):
        """Test get_json_from_url handles unsupported types"""
        mock_json_data = {
            "id": "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4",
            "type": "UnsupportedType",  # This should cause a ValueError
            "title": "Test Movie",
            "external_resources": [],
        }

        mock_instance = mock_downloader.return_value
        mock_instance.download.return_value.json.return_value = mock_json_data

        with pytest.raises(ValueError, match="Not a supported format or type"):
            FediverseInstance.get_json_from_url(
                "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"
            )

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
                "check_model_compatibility": lambda self,
                model_cls: False,  # Incompatible
            },
        )()
        mock_site_manager.return_value = mock_book_site

        site = FediverseInstance("https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4")
        content = site.scrape()

        # Should not include the incompatible external resource in lookup_ids
        assert IdType.ISBN not in content.lookup_ids

    def test_real_url_parsing(self):
        """Test URL parsing with the specific provided URL"""
        test_url = "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"
        expected_id = "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4"

        result = FediverseInstance.url_to_id(test_url)
        assert result == expected_id

        # Test with query parameters
        test_url_with_params = (
            "https://eggplant.place/movie/6AUSh8LPNYZZBTZcsDtxo4?param=value"
        )
        result_with_params = FediverseInstance.url_to_id(test_url_with_params)
        assert result_with_params == expected_id

    def test_url_to_id_api_removal(self):
        """Test URL to ID conversion removes 'api/' prefix"""
        test_url = "https://example.com/api/movie/123"
        expected_id = "https://example.com/movie/123"

        result = FediverseInstance.url_to_id(test_url)
        assert result == expected_id
