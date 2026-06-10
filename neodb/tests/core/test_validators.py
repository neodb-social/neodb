import socket
from unittest.mock import patch

from django.http import HttpRequest

from common.validators import (
    get_safe_redirect_url,
    get_safe_referer_url,
    is_safe_url,
    is_valid_url,
    sanitize_next_url,
)


class TestIsSafeUrl:
    def test_same_host_is_safe(self):
        assert is_safe_url("http://example.org/path") is True

    def test_relative_url_is_safe(self):
        assert is_safe_url("/some/path") is True

    def test_external_host_is_unsafe(self):
        assert is_safe_url("http://evil.com/steal") is False

    def test_none_is_unsafe(self):
        assert is_safe_url(None) is False

    def test_empty_string_is_unsafe(self):
        assert is_safe_url("") is False

    def test_explicit_allowed_hosts(self):
        assert (
            is_safe_url("http://trusted.com/page", allowed_hosts={"trusted.com"})
            is True
        )
        assert (
            is_safe_url("http://other.com/page", allowed_hosts={"trusted.com"}) is False
        )


class TestGetSafeRedirectUrl:
    def test_safe_url_returned(self):
        assert get_safe_redirect_url("/dashboard") == "/dashboard"

    def test_unsafe_url_returns_default(self):
        assert get_safe_redirect_url("http://evil.com/") == "/"

    def test_none_returns_default(self):
        assert get_safe_redirect_url(None) == "/"

    def test_custom_default(self):
        assert get_safe_redirect_url("http://evil.com/", default="/home") == "/home"

    def test_same_host_returned(self):
        assert get_safe_redirect_url("http://example.org/ok") == "http://example.org/ok"


class TestSanitizeNextUrl:
    def test_safe_url_returned(self):
        assert sanitize_next_url("/next") == "/next"

    def test_unsafe_url_returns_none(self):
        assert sanitize_next_url("http://evil.com/") is None

    def test_none_returns_none(self):
        assert sanitize_next_url(None) is None


class TestGetSafeRefererUrl:
    def _request(self, referer=None):
        req = HttpRequest()
        if referer:
            req.META["HTTP_REFERER"] = referer
        return req

    def test_safe_referer_returned(self):
        req = self._request("http://example.org/page")
        assert get_safe_referer_url(req) == "http://example.org/page"

    def test_unsafe_referer_returns_default(self):
        req = self._request("http://evil.com/")
        assert get_safe_referer_url(req) == "/"

    def test_missing_referer_returns_default(self):
        req = self._request()
        assert get_safe_referer_url(req) == "/"

    def test_custom_default(self):
        req = self._request("http://evil.com/")
        assert get_safe_referer_url(req, default="/home") == "/home"


def _make_addr_info(ip: str):
    """Build a fake getaddrinfo result for the given IP address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


class TestIsValidUrl:
    def test_valid_public_url(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("93.184.216.34")):
            assert is_valid_url("http://example.com/path") is True

    def test_loopback_ip_blocked(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("127.0.0.1")):
            assert is_valid_url("http://localhost/") is False

    def test_private_ip_blocked(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("192.168.1.1")):
            assert is_valid_url("http://internal.local/") is False

    def test_dns_failure_returns_false(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            assert is_valid_url("http://doesnotexist.invalid/") is False

    def test_none_returns_false(self):
        assert is_valid_url(None) is False

    def test_invalid_format_returns_false(self):
        assert is_valid_url("not a url") is False

    def test_empty_string_returns_false(self):
        assert is_valid_url("") is False
