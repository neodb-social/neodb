import pytest
from django.conf import settings
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from common.utils import client_ip


def _request(xff: str | None = None) -> HttpRequest:
    request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.5")
    if xff is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = xff
    return request


def test_default_depth_is_one() -> None:
    assert settings.TRUSTED_PROXY_DEPTH == 1


@pytest.mark.parametrize(
    "depth,xff,expected",
    [
        (1, "198.51.100.1", "198.51.100.1"),
        # a forged leading entry is ignored: the front proxy appends the peer
        (1, "6.6.6.6, 198.51.100.1", "198.51.100.1"),
        (2, "6.6.6.6, 203.0.113.9, 198.51.100.1", "203.0.113.9"),
        (2, "198.51.100.1", "172.18.0.5"),
        (1, None, "172.18.0.5"),
        (1, " , ", "172.18.0.5"),
        (0, "6.6.6.6, 198.51.100.1", "172.18.0.5"),
    ],
)
def test_client_ip(depth: int, xff: str | None, expected: str) -> None:
    with override_settings(TRUSTED_PROXY_DEPTH=depth):
        assert client_ip(_request(xff)) == expected


def test_real_ip_header_is_not_trusted() -> None:
    request = _request("198.51.100.1")
    request.META["HTTP_X_REAL_IP"] = "6.6.6.6"
    assert client_ip(request) == "198.51.100.1"
