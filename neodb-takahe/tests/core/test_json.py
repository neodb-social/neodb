import httpx

from core.json import find_ap_alternate


def _resp(url: str, *, content: bytes = b"", headers: list[tuple[str, str]] = None):
    return httpx.Response(
        status_code=200,
        headers=headers or [],
        content=content,
        request=httpx.Request("GET", url),
    )


def test_find_ap_alternate_from_link_header():
    """WordPress AP plugin: HTML body + Link header pointing at the AP object."""
    response = _resp(
        "https://blog.example/2026/04/22/post-slug/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html; charset=UTF-8"),
            (
                "link",
                "<https://blog.example/?p=15463>; "
                'rel="alternate"; type="application/activity+json"',
            ),
        ],
    )
    assert find_ap_alternate(response) == "https://blog.example/?p=15463"


def test_find_ap_alternate_picks_ap_among_multiple_alternates():
    """A page can advertise several alternates; we want the AP one."""
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
    assert find_ap_alternate(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_resolves_relative_url():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            ("link", '</?p=42>; rel="alternate"; type="application/activity+json"'),
        ],
    )
    assert find_ap_alternate(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_from_html_link_tag():
    """Fallback to <link rel="alternate" type="application/activity+json"> in HTML."""
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
    assert find_ap_alternate(response) == "https://blog.example/?p=42"


def test_find_ap_alternate_returns_none_when_absent():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html><head></head></html>",
        headers=[("content-type", "text/html")],
    )
    assert find_ap_alternate(response) is None


def test_find_ap_alternate_ignores_non_alternate_rels():
    response = _resp(
        "https://blog.example/post/",
        content=b"<html></html>",
        headers=[
            ("content-type", "text/html"),
            (
                "link",
                '<https://blog.example/?p=42>; rel="self"; type="application/activity+json"',
            ),
        ],
    )
    assert find_ap_alternate(response) is None
