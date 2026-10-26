from unittest import mock

import pytest

from mastodon.models import mastodon as mastodon_module
from mastodon.models.mastodon import detect_server_info


def _response(status: int, json_data=None, text: str = "", headers=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    resp.headers = headers or {}
    if json_data is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = json_data
    return resp


def _fake_get(routes: dict[str, mock.Mock]):
    def get(url, **kwargs):
        return routes.get(url, _response(404))

    return get


INSTANCE = {"uri": "example.com", "version": "4.3.0"}


@pytest.fixture(autouse=True)
def _public_urls(monkeypatch):
    monkeypatch.setattr(mastodon_module, "is_valid_url", lambda url: True)


def test_split_domain_with_host_meta_redirect_is_accepted():
    routes = {
        "https://web.example.com/api/v1/instance": _response(200, INSTANCE),
        "https://example.com/.well-known/host-meta": _response(
            301, headers={"location": "https://web.example.com/.well-known/host-meta"}
        ),
    }
    with mock.patch.object(mastodon_module, "get", side_effect=_fake_get(routes)):
        assert detect_server_info("web.example.com") == (
            "example.com",
            "web.example.com",
            "4.3.0",
        )


def test_split_domain_with_lrdd_template_is_accepted():
    routes = {
        "https://web.example.com/api/v1/instance": _response(200, INSTANCE),
        "https://example.com/.well-known/host-meta": _response(
            200,
            text='<XRD><Link rel="lrdd" template="https://web.example.com/.well-known/webfinger?resource={uri}"/></XRD>',
        ),
    }
    with mock.patch.object(mastodon_module, "get", side_effect=_fake_get(routes)):
        assert detect_server_info("web.example.com")[1] == "web.example.com"


def test_claimed_domain_without_delegation_is_refused():
    routes = {"https://evil.example/api/v1/instance": _response(200, INSTANCE)}
    with (
        mock.patch.object(mastodon_module, "get", side_effect=_fake_get(routes)),
        pytest.raises(Exception, match="invalid data"),
    ):
        detect_server_info("evil.example")


def test_domain_serving_its_own_api_needs_no_delegation():
    routes = {
        "https://web.example.com/api/v1/instance": _response(200, INSTANCE),
        "https://example.com/api/v1/instance": _response(200, INSTANCE),
    }
    with mock.patch.object(mastodon_module, "get", side_effect=_fake_get(routes)):
        assert detect_server_info("web.example.com")[1] == "example.com"
