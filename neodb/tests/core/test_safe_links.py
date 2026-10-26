import pytest

from common.templatetags.strip_scheme import http_href
from takahe.uris import ProxyAbsoluteUrl


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://example.org/show", "https://example.org/show"),
        ("http://example.org/show", "http://example.org/show"),
        ("javascript://%0aalert(1)", ""),
        ("javascript:alert(1)", ""),
        ("", ""),
    ],
)
def test_http_href_keeps_only_web_links(value: str, expected: str) -> None:
    assert http_href(value) == expected


def test_proxy_url_extension_is_a_plain_word() -> None:
    good = ProxyAbsoluteUrl("/proxy/x/", remote_url="https://r.test/a/b.PNG")
    assert good.relative.endswith(".PNG")

    bad = ProxyAbsoluteUrl(
        "/proxy/x/", remote_url="https://r.test/a/b.png');position:fixed;x"
    )
    assert bad.relative.endswith(".bin")
