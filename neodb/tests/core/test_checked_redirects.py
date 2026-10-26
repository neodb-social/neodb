import socket
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import RequestException

from catalog.common.downloaders import (
    RESPONSE_NETWORK_ERROR,
    BasicDownloader,
    BasicImageDownloader,
    request_with_checked_redirects,
)
from catalog.sites.rss import _CheckedRedirectHandler

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "10.0.0.5"


def _addr_info(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _getaddrinfo_for(hosts: dict[str, str]):
    def fake(hostname, *args, **kwargs):
        return _addr_info(hosts[hostname])

    return fake


def _response(status: int, location: str | None = None, url: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"location": location} if location else {}
    resp.is_redirect = location is not None and status in (301, 302, 307, 308)
    resp.url = url
    resp.text = ""
    return resp


def test_public_redirect_chain_is_followed() -> None:
    hops = [
        _response(302, "/moved", "https://public.example/a"),
        _response(301, "https://other.example/b", "https://public.example/moved"),
        _response(200, url="https://other.example/b"),
    ]
    hosts = {"public.example": PUBLIC_IP, "other.example": PUBLIC_IP}
    with (
        patch("socket.getaddrinfo", side_effect=_getaddrinfo_for(hosts)),
        patch("requests.Session.request", side_effect=hops) as req,
    ):
        resp = request_with_checked_redirects("GET", "https://public.example/a")

    assert resp.status_code == 200
    assert [c.args[1] for c in req.call_args_list] == [
        "https://public.example/a",
        "https://public.example/moved",
        "https://other.example/b",
    ]
    assert all(c.kwargs["allow_redirects"] is False for c in req.call_args_list)


def test_redirect_to_private_host_is_refused() -> None:
    hops = [_response(302, "http://internal.example:8080/admin")]
    hosts = {"public.example": PUBLIC_IP, "internal.example": PRIVATE_IP}
    with (
        patch("socket.getaddrinfo", side_effect=_getaddrinfo_for(hosts)),
        patch("requests.Session.request", side_effect=hops) as req,
        pytest.raises(RequestException),
    ):
        request_with_checked_redirects("GET", "https://public.example/a")

    assert req.call_count == 1


def test_downloader_reports_refused_redirect_as_network_error() -> None:
    hops = [_response(302, "http://internal.example/")]
    hosts = {"public.example": PUBLIC_IP, "internal.example": PRIVATE_IP}
    with (
        patch("socket.getaddrinfo", side_effect=_getaddrinfo_for(hosts)),
        patch("requests.Session.request", side_effect=hops),
    ):
        resp, response_type = BasicDownloader("https://public.example/a")._download(
            "https://public.example/a"
        )

    assert resp is None
    assert response_type == RESPONSE_NETWORK_ERROR


def test_rss_opener_refuses_private_redirect() -> None:
    handler = _CheckedRedirectHandler()
    req = urllib.request.Request("https://public.example/feed.xml")
    with (
        patch("socket.getaddrinfo", return_value=_addr_info(PRIVATE_IP)),
        pytest.raises(urllib.error.HTTPError),
    ):
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://internal.example/"
        )


def test_image_referer_does_not_leak_into_shared_headers() -> None:
    before = dict(BasicDownloader.headers)
    dl = BasicImageDownloader("https://public.example/a.jpg", "https://public.example/")

    assert dl.headers["Referer"] == "https://public.example/"
    assert BasicDownloader.headers == before
    assert "Referer" not in BasicDownloader.headers
