import logging
from types import SimpleNamespace
from unittest import mock

from catalog.common.downloaders import RESPONSE_INVALID_CONTENT, DownloadError
from catalog.common.sites import SiteManager
from journal.importers.steam import SteamImporter


def test_fetch_failure_log_redacts_api_key(caplog) -> None:
    importer = SteamImporter()
    importer.steam_apikey = "SECRETKEY123"
    failed = DownloadError(
        SimpleNamespace(
            url="https://store.steampowered.com/api/appdetails?appids=1&key=SECRETKEY123",
            logs=[],
            response_type=RESPONSE_INVALID_CONTENT,
        )
    )
    site = mock.Mock()
    site.get_item.return_value = None
    site.get_resource_ready.side_effect = failed

    with (
        mock.patch.object(SiteManager, "get_site_by_id", return_value=site),
        caplog.at_level(logging.ERROR),
    ):
        assert importer.get_item_by_id("1") is None

    assert "appdetails" in caplog.text
    assert "SECRETKEY123" not in caplog.text
