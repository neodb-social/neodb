from typing import Any

import pytest

from mastodon.models import mastodon as mastodon_api


class FakeResponse:
    def __init__(
        self, status_code: int, data: Any = None, headers: dict | None = None
    ) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

    def json(self) -> Any:
        return self._data


def test_request_never_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse(302, headers={"Location": "http://127.0.0.1/"})

    monkeypatch.setattr(mastodon_api.requests, "request", fake_request)
    response = mastodon_api.get("https://example.org/", allow_redirects=True)
    mastodon_api.post("https://example.org/api/v1/apps", data={})
    assert response.status_code == 302
    assert [c["allow_redirects"] for c in calls] == [False, False]


@pytest.mark.django_db(databases="__all__")
def test_redirected_instance_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mastodon_api, "is_valid_url", lambda url: True)
    monkeypatch.setattr(
        mastodon_api.requests,
        "request",
        lambda method, url, **kw: FakeResponse(301, headers={"Location": "/x"}),
    )
    with pytest.raises(Exception, match="returned error code 301"):
        mastodon_api.detect_server_info("example.org")


@pytest.mark.django_db(databases="__all__")
def test_related_accounts_follow_next_link_on_api_host_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[tuple[str, str]] = []
    pages = {
        "https://example.org/api/v1/follows": FakeResponse(
            200,
            [{"acct": "a"}],
            {"Link": '<https://example.org/api/v1/follows?max_id=2>; rel="next"'},
        ),
        "https://example.org/api/v1/follows?max_id=2": FakeResponse(
            200,
            [{"acct": "b@other.example"}],
            {"Link": '<https://attacker.example/collect>; rel="next"'},
        ),
    }

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        fetched.append((url, kwargs["headers"]["Authorization"]))
        return pages[url]

    monkeypatch.setattr(mastodon_api.requests, "request", fake_request)
    result = mastodon_api.get_related_acct_list("example.org", "tok", "/api/v1/follows")
    assert result == ["a@example.org", "b@other.example"]
    assert [url for url, _ in fetched] == list(pages)


@pytest.mark.django_db(databases="__all__")
def test_related_accounts_ignore_plain_http_next_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        fetched.append(url)
        return FakeResponse(
            200, [], {"Link": '<http://example.org/api/v1/follows?p=2>; rel="next"'}
        )

    monkeypatch.setattr(mastodon_api.requests, "request", fake_request)
    mastodon_api.get_related_acct_list("example.org", "tok", "/api/v1/follows")
    assert fetched == ["https://example.org/api/v1/follows"]
