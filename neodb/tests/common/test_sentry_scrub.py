from typing import Any

from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

from common.sentry import SENTRY_EXTRA_DENYLIST, before_breadcrumb, before_send


def _send(event: Any) -> Any:
    return before_send(event, {})


def test_before_send_drops_cookies_and_secret_query() -> None:
    event = {
        "request": {
            "url": "https://site.example/api/me?access_token=abc",
            "query_string": "access_token=abc&page=2",
            "cookies": {"neodbsid": "secret"},
            "headers": {"Cookie": "neodbsid=secret", "Accept": "text/html"},
        }
    }
    request = _send(event)["request"]
    assert "cookies" not in request
    assert request["headers"] == {"Cookie": "[Filtered]", "Accept": "text/html"}
    assert request["url"] == "https://site.example/api/me"
    assert request["query_string"] == ""


def test_before_send_keeps_clean_request() -> None:
    event = {
        "request": {
            "url": "https://site.example/search?q=dune",
            "query_string": "q=dune",
        }
    }
    assert _send(event) == {
        "request": {
            "url": "https://site.example/search?q=dune",
            "query_string": "q=dune",
        }
    }


def test_before_send_scrubs_transaction_spans() -> None:
    event = {
        "spans": [
            {"data": {"url": "https://api.example/x?key=k", "http.query": "key=k"}},
            {"data": {"url": "https://api.example/y", "http.query": "page=2"}},
            {"op": "db"},
        ]
    }
    spans = _send(event)["spans"]
    assert spans[0]["data"] == {"url": "https://api.example/x", "http.query": ""}
    assert spans[1]["data"] == {"url": "https://api.example/y", "http.query": "page=2"}


def test_before_breadcrumb_blanks_secret_query() -> None:
    for query in ("api_key=k", "a=1&token=t", "client_secret=s"):
        crumb = {"data": {"url": "https://api.example/a", "http.query": query}}
        assert before_breadcrumb(crumb, {})["data"]["http.query"] == ""
    crumb = {"data": {"url": "https://api.example/a?api_key=k"}}
    assert before_breadcrumb(crumb, {})["data"]["url"] == "https://api.example/a"


def test_before_breadcrumb_keeps_lookalike_keys() -> None:
    crumb = {
        "data": {"url": "https://api.example/a", "http.query": "monkey=1&tokens=2"}
    }
    assert before_breadcrumb(crumb, {}) == {
        "data": {"url": "https://api.example/a", "http.query": "monkey=1&tokens=2"}
    }
    assert before_breadcrumb({"message": "m"}, {}) == {"message": "m"}


def test_scrubber_masks_oauth_form_fields() -> None:
    scrubber = EventScrubber(
        denylist=[*DEFAULT_DENYLIST, *SENTRY_EXTRA_DENYLIST], send_default_pii=True
    )
    event: Any = {
        "request": {
            "data": {"client_secret": "cs", "refresh_token": "rt", "grant_type": "x"}
        }
    }
    scrubber.scrub_event(event)
    data = event["request"]["data"]
    assert data["grant_type"] == "x"
    assert data["client_secret"] != "cs"
    assert data["refresh_token"] != "rt"
