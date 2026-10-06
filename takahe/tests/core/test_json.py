import httpx
import pytest

from core.json import clean_json, find_ap_alternate, json_from_response


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


def test_clean_json_returns_same_object_when_clean():
    data = {"a": ["b", 1, 2.5, None, True, {"c": "\U0001f600"}]}
    assert clean_json(data) is data


def test_clean_json_removes_nul_replaces_surrogates_and_nan():
    data = {
        "na\x00me": "a\x00b",
        "list": ["\ud83d cut", ("x\x00",)],
        "n": float("nan"),
        "inf": float("-inf"),
        "ok": "\U0001f600",
    }
    assert clean_json(data) == {
        "name": "ab",
        "list": ["� cut", ["x"]],
        "n": None,
        "inf": None,
        "ok": "\U0001f600",
    }


def test_json_from_response_cleans_escapes():
    expected = {"name": "ab", "summary": "�", "n": None}
    body = b'{"name": "a\\u0000b", "summary": "\\ud83d", "n": NaN}'
    response = _resp(
        "https://remote.example/users/a",
        content=body,
        headers=[("content-type", "application/activity+json")],
    )
    assert json_from_response(response) == expected
    response = _resp(
        "https://remote.example/users/a",
        content=body,
        headers=[("content-type", "application/activity+json; charset=utf-8")],
    )
    assert json_from_response(response) == expected


@pytest.mark.parametrize("charset", ["bogus", "a=b"])
def test_json_from_response_ignores_unknown_charset(charset):
    """An unusable charset parameter does not fail the caller."""
    response = _resp(
        "https://remote.example/users/a",
        content=b'{"id": "https://remote.example/users/a"}',
        headers=[
            ("content-type", f"application/activity+json; charset={charset}"),
        ],
    )
    assert json_from_response(response) == {"id": "https://remote.example/users/a"}
