import socket
from unittest.mock import patch

import httpx
import pytest
from activities.models import Post, PostInteraction
from api.pagination import MastodonPaginator
from core.exceptions import ActivityPubFormatError
from core.files import SSRFAttemptError, check_url_safety


@pytest.mark.django_db
def test_signed_get_refuses_a_cross_host_redirect(identity, httpx_mock):
    httpx_mock.add_response(
        url="https://peer.test/notes/1",
        status_code=302,
        headers={"location": "https://evil.test/notes/1"},
    )

    with pytest.raises(httpx.ConnectError):
        identity.signed_request(method="get", uri="https://peer.test/notes/1")


@pytest.mark.django_db
def test_signed_get_treats_a_malformed_redirect_as_a_connection_error(
    identity, httpx_mock
):
    httpx_mock.add_response(
        url="https://peer.test/notes/1",
        status_code=302,
        headers={"location": "https://peer.test/notes/\x01"},
    )

    # httpx raises its own request error for this header; a target that
    # fails only on join is turned into a ConnectError, which callers handle
    with pytest.raises(httpx.RequestError):
        identity.signed_request(method="get", uri="https://peer.test/notes/1")


@pytest.mark.django_db
def test_signed_get_follows_a_same_host_redirect(identity, httpx_mock):
    httpx_mock.add_response(
        url="https://peer.test/notes/1",
        status_code=302,
        headers={"location": "https://peer.test/objects/1"},
    )
    httpx_mock.add_response(url="https://peer.test/objects/1", json={"id": "x"})

    response = identity.signed_request(method="get", uri="https://peer.test/notes/1")

    assert response.status_code == 200


@pytest.mark.django_db
def test_interaction_id_must_be_on_the_actor_host(remote_identity):
    with pytest.raises(ActivityPubFormatError):
        PostInteraction.by_ap(
            {
                "id": "https://mastodon.test/users/victim#likes/1",
                "type": "Like",
                "actor": remote_identity.actor_uri,
                "object": "https://example.com/posts/1",
            },
            create=True,
        )


def _addr_info(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


@pytest.mark.parametrize("ip", ["100.100.100.200", "100.64.0.1", "10.0.0.1"])
def test_url_safety_blocks_non_global_addresses(ip: str, monkeypatch):
    # the autouse fixture bypasses the hook, so call the real one
    from core import files

    monkeypatch.setattr(files, "check_url_safety", check_url_safety)
    request = httpx.Request("GET", "https://meta.test/")
    with (
        patch("socket.getaddrinfo", return_value=_addr_info(ip)),
        pytest.raises(SSRFAttemptError),
    ):
        check_url_safety(request)


def test_url_safety_allows_a_public_address():
    request = httpx.Request("GET", "https://public.test/")
    with patch("socket.getaddrinfo", return_value=_addr_info("93.184.216.34")):
        check_url_safety(request)


@pytest.mark.django_db
def test_paginator_ignores_malformed_ids_and_limits():
    result = MastodonPaginator().paginate(
        Post.objects.all(),
        min_id="not-an-id",
        max_id="interaction-12",
        since_id="²",
        limit=-1,
    )

    assert result.results == []
    assert result.limit == 1
