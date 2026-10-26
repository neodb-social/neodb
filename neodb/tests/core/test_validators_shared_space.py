import socket
from unittest.mock import patch

import pytest

from common.validators import _host_cache, is_valid_url


def _addr_info(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


@pytest.mark.parametrize("ip", ["100.100.100.200", "100.64.0.1", "192.0.0.8"])
def test_shared_address_space_is_blocked(ip: str) -> None:
    _host_cache.clear()
    with patch("socket.getaddrinfo", return_value=_addr_info(ip)):
        assert is_valid_url("https://metadata.example/") is False
    _host_cache.clear()
