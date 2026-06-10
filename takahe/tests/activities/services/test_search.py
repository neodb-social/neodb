import httpx
import pytest

from activities.models import Post
from activities.services.search import SearchService
from users.models.system_actor import SystemActor


@pytest.mark.django_db
def test_search_url_follows_ap_alternate(monkeypatch, config_system):
    """A permalink that returns HTML with an AP alternate Link should
    cause search_url to refetch the alternate URL before giving up.

    Regression: WordPress's ActivityPub plugin does not content-negotiate
    permalinks, so the AP object is only reachable via the Link header.
    """
    permalink = "https://blog.example/2026/04/22/post-slug/"
    ap_object_url = "https://blog.example/?p=42"

    html_response = httpx.Response(
        200,
        headers={
            "Content-Type": "text/html; charset=UTF-8",
            "Link": (
                f'<{ap_object_url}>; rel="alternate"; type="application/activity+json"'
            ),
        },
        content=b"<html><body>not JSON</body></html>",
        request=httpx.Request("GET", permalink),
    )
    ap_response = httpx.Response(
        200,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": ap_object_url,
            "type": "Article",
            "attributedTo": "https://blog.example/?author=0",
            "content": "<p>Article body</p>",
            "published": "2026-04-22T12:00:00Z",
        },
        request=httpx.Request("GET", ap_object_url),
    )

    calls: list[str] = []

    def fake_signed_request(self, method, uri, body=None):
        calls.append(uri)
        if uri == permalink:
            return html_response
        if uri == ap_object_url:
            return ap_response
        raise AssertionError(f"unexpected uri: {uri}")

    monkeypatch.setattr(SystemActor, "signed_request", fake_signed_request)

    captured: dict = {}

    def fake_by_object_uri(cls, uri, fetch=False, fetch_as=None):
        captured["uri"] = uri
        captured["fetch"] = fetch
        raise Post.DoesNotExist()

    monkeypatch.setattr(Post, "by_object_uri", classmethod(fake_by_object_uri))

    result = SearchService(permalink, None).search_url()

    assert calls == [permalink, ap_object_url], (
        "search_url should follow the AP alternate Link header"
    )
    assert captured["uri"] == ap_object_url
    assert captured["fetch"] is True
    assert result is None


@pytest.mark.django_db
def test_search_url_gives_up_when_no_alternate(monkeypatch, config_system):
    """If the response is HTML without an AP alternate hint, search_url
    must give up rather than loop or raise."""
    url = "https://blog.example/no-ap/"
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/html"},
        content=b"<html><head></head><body>nope</body></html>",
        request=httpx.Request("GET", url),
    )

    calls: list[str] = []

    def fake_signed_request(self, method, uri, body=None):
        calls.append(uri)
        return response

    monkeypatch.setattr(SystemActor, "signed_request", fake_signed_request)

    assert SearchService(url, None).search_url() is None
    assert calls == [url]


@pytest.mark.django_db
def test_search_url_does_not_loop_on_self_referential_alternate(
    monkeypatch, config_system
):
    """A misconfigured alternate that points back at the same URL must
    not cause an infinite loop."""
    url = "https://blog.example/loop/"
    response = httpx.Response(
        200,
        headers={
            "Content-Type": "text/html",
            "Link": f'<{url}>; rel="alternate"; type="application/activity+json"',
        },
        content=b"<html></html>",
        request=httpx.Request("GET", url),
    )

    call_count = 0

    def fake_signed_request(self, method, uri, body=None):
        nonlocal call_count
        call_count += 1
        return response

    monkeypatch.setattr(SystemActor, "signed_request", fake_signed_request)

    assert SearchService(url, None).search_url() is None
    assert call_count == 1
