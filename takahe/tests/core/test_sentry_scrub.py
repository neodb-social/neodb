from sentry_sdk.scrubber import EventScrubber

from takahe import settings as takahe_settings


def test_before_send_drops_cookies_and_secret_query() -> None:
    event = {
        "request": {
            "url": "https://site.example/api/v1/streaming?access_token=abc",
            "query_string": "stream=user&access_token=abc",
            "cookies": {"neodbsid": "secret", "sessionid": "x"},
            "headers": {"Cookie": "neodbsid=secret", "Accept": "text/html"},
        }
    }
    request = takahe_settings.sentry_before_send(event, {})["request"]
    assert "cookies" not in request
    assert request["headers"] == {"Cookie": "[Filtered]", "Accept": "text/html"}
    assert request["url"] == "https://site.example/api/v1/streaming"
    assert request["query_string"] == ""


def test_before_send_keeps_clean_url() -> None:
    event = {
        "request": {
            "url": "https://site.example/tags/x?page=2",
            "query_string": "page=2",
        }
    }
    assert takahe_settings.sentry_before_send(event, {}) == {
        "request": {
            "url": "https://site.example/tags/x?page=2",
            "query_string": "page=2",
        }
    }


def test_before_send_scrubs_transaction_spans() -> None:
    event = {
        "spans": [
            {"data": {"url": "https://r.example/x?token=t", "http.query": "token=t"}},
            {"op": "db"},
        ]
    }
    spans = takahe_settings.sentry_before_send(event, {})["spans"]
    assert spans[0]["data"] == {"url": "https://r.example/x", "http.query": ""}


def test_before_breadcrumb_blanks_api_key_query() -> None:
    crumb = {"data": {"url": "https://r.example/a", "http.query": "api_key=k&x=1"}}
    assert takahe_settings.sentry_before_breadcrumb(crumb, {})["data"] == {
        "url": "https://r.example/a",
        "http.query": "",
    }
    crumb = {"data": {"url": "https://r.example/a?client_secret=s"}}
    data = takahe_settings.sentry_before_breadcrumb(crumb, {})["data"]
    assert data["url"] == "https://r.example/a"


def test_before_breadcrumb_keeps_clean_url() -> None:
    crumb = {"data": {"url": "https://r.example/a", "http.query": "page=2"}}
    assert takahe_settings.sentry_before_breadcrumb(crumb, {}) == {
        "data": {"url": "https://r.example/a", "http.query": "page=2"}
    }


def test_scrubber_masks_neodb_session_cookie() -> None:
    scrubber = EventScrubber(
        denylist=takahe_settings.SENTRY_DENYLIST, send_default_pii=True
    )
    event = {"request": {"cookies": {"neodbsid": "secret", "theme": "dark"}}}
    scrubber.scrub_event(event)
    cookies = event["request"]["cookies"]
    assert cookies["theme"] == "dark"
    assert cookies["neodbsid"] != "secret"
