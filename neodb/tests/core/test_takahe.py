import httpx

from takahe.management.commands.fetch import find_ap_alternate_url


def _resp(url: str, *, content: bytes = b"", headers=None) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers=headers or [],
        content=content,
        request=httpx.Request("GET", url),
    )


def test_find_ap_alternate_url_from_link_header():
    """WordPress AP plugin: HTML body + Link header pointing at the AP object."""
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html; charset=UTF-8"),
            (
                "link",
                '<https://blog.example/?p=42>; rel="alternate"; '
                'type="application/activity+json"',
            ),
        ],
    )
    assert find_ap_alternate_url(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_url_picks_ap_among_multiple_alternates():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            (
                "link",
                '<https://blog.example/feed/>; rel="alternate"; type="application/rss+xml",'
                ' <https://blog.example/?p=42>; rel="alternate"; type="application/activity+json"',
            ),
        ],
    )
    assert find_ap_alternate_url(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_url_resolves_relative_url():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            ("link", '</?p=42>; rel="alternate"; type="application/activity+json"'),
        ],
    )
    assert find_ap_alternate_url(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_url_from_html_link_tag():
    """Fall back to <link rel="alternate" type="application/activity+json">
    in the HTML head when no Link header is present."""
    body = (
        b"<html><head>"
        b'<link rel="alternate" type="application/rss+xml" href="/feed/">'
        b'<link rel="alternate" type="application/activity+json"'
        b' href="https://blog.example/?p=42">'
        b"</head></html>"
    )
    response = _resp(
        "https://blog.example/post/",
        content=body,
        headers=[("content-type", "text/html; charset=utf-8")],
    )
    assert find_ap_alternate_url(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_url_returns_none_without_hints():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html><head></head></html>",
        headers=[("content-type", "text/html")],
    )
    assert find_ap_alternate_url(response) is None


def test_find_ap_alternate_url_ignores_non_ap_alternates():
    """A bare ``application/json`` alternate must not be auto-followed as AP."""
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            (
                "link",
                '<https://blog.example/api/post/42>; rel="alternate"; '
                'type="application/json"',
            ),
        ],
    )
    assert find_ap_alternate_url(response) is None


def test_find_ap_alternate_url_ignores_non_alternate_rels():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            (
                "link",
                '<https://blog.example/?p=42>; rel="self"; '
                'type="application/activity+json"',
            ),
        ],
    )
    assert find_ap_alternate_url(response) is None
