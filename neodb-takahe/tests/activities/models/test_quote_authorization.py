import json

import pytest
from activities.models import Post, QuoteAuthorization
from django.test import Client

from users.models import Identity


@pytest.fixture(autouse=True)
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


@pytest.mark.django_db
def test_to_ap_shape(identity: Identity, config_system):
    post = Post.create_local(
        author=identity, content="hi", visibility=Post.Visibilities.public
    )
    auth = QuoteAuthorization.objects.create(
        target_post=post,
        interacting_object_uri="https://remote.test/users/x/statuses/1",
        request_uri="https://remote.test/users/x/quote-request/1",
    )
    ap = auth.to_ap()
    assert ap["type"] == "QuoteAuthorization"
    assert ap["id"] == auth.object_uri
    assert ap["attributedTo"] == identity.actor_uri
    assert ap["interactingObject"] == "https://remote.test/users/x/statuses/1"
    assert ap["interactionTarget"] == post.object_uri


@pytest.mark.django_db
def test_url_is_post_scoped(identity: Identity, config_system):
    post = Post.create_local(author=identity, content="hi")
    auth = QuoteAuthorization.objects.create(
        target_post=post,
        interacting_object_uri="https://remote.test/users/x/statuses/1",
    )
    url = auth.object_uri
    assert url.startswith(post.object_uri)
    assert url.endswith(f"/quote-auth/{auth.id}/")


@pytest.mark.django_db
def test_view_serves_authorization(identity: Identity, config_system):
    post = Post.create_local(
        author=identity, content="hi", visibility=Post.Visibilities.public
    )
    auth = QuoteAuthorization.objects.create(
        target_post=post,
        interacting_object_uri="https://remote.test/users/x/statuses/1",
    )
    client = Client(HTTP_HOST="example.com")
    path = f"/@{identity.username}@{identity.domain.domain}/posts/{post.id}/quote-auth/{auth.id}/"
    resp = client.get(path, headers={"accept": "application/activity+json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/activity+json")
    body = json.loads(resp.content)
    assert body["type"] == "QuoteAuthorization"
    assert body["id"].endswith(f"/quote-auth/{auth.id}/")
    assert body["attributedTo"] == identity.actor_uri
    assert body["interactingObject"] == "https://remote.test/users/x/statuses/1"
    assert body["interactionTarget"] == post.object_uri


@pytest.mark.django_db
def test_view_404_for_mismatched_post(
    identity: Identity, other_identity: Identity, config_system
):
    post_a = Post.create_local(author=identity, content="a")
    post_b = Post.create_local(author=other_identity, content="b")
    auth = QuoteAuthorization.objects.create(
        target_post=post_a,
        interacting_object_uri="https://remote.test/users/x/statuses/1",
    )
    client = Client(HTTP_HOST="example.com")
    # Wrong post id under a's handle.
    path = f"/@{identity.username}@{identity.domain.domain}/posts/{post_b.id}/quote-auth/{auth.id}/"
    assert (
        client.get(path, headers={"accept": "application/activity+json"}).status_code
        == 404
    )
    # Wrong handle for the auth's post.
    path = f"/@{other_identity.username}@{other_identity.domain.domain}/posts/{post_a.id}/quote-auth/{auth.id}/"
    assert (
        client.get(path, headers={"accept": "application/activity+json"}).status_code
        == 404
    )


@pytest.mark.django_db
def test_handle_quote_request_persists_and_uses_url(
    monkeypatch, identity: Identity, remote_identity: Identity, config_system
):
    post = Post.create_local(
        author=identity, content="hi", visibility=Post.Visibilities.public
    )

    sent: dict = {}

    def fake_signed_request(self, method, uri, body=None):
        sent["uri"] = uri
        sent["body"] = body
        return None

    monkeypatch.setattr(Identity, "signed_request", fake_signed_request)

    Post.handle_quote_request_ap(
        {
            "type": "QuoteRequest",
            "id": "https://remote.test/users/quoter/quote-request/1",
            "actor": remote_identity.actor_uri,
            "object": post.object_uri,
            "instrument": "https://remote.test/users/quoter/statuses/42",
        }
    )

    auth = QuoteAuthorization.objects.get(target_post=post)
    assert auth.interacting_object_uri == "https://remote.test/users/quoter/statuses/42"
    assert auth.request_uri == "https://remote.test/users/quoter/quote-request/1"

    assert sent["uri"] == remote_identity.inbox_uri
    accept = sent["body"]
    assert accept["type"] == "Accept"
    # The Accept's result is the QuoteAuthorization with a real URL, not a fragment.
    result = accept["result"]
    assert result["type"] == "QuoteAuthorization"
    assert result["id"] == auth.object_uri
    assert "#" not in result["id"]
    assert result["attributedTo"] == identity.actor_uri
    assert result["interactingObject"] == "https://remote.test/users/quoter/statuses/42"
    assert result["interactionTarget"] == post.object_uri


@pytest.mark.django_db
def test_handle_quote_request_rejects_non_public(
    monkeypatch, identity: Identity, remote_identity: Identity, config_system
):
    post = Post.create_local(
        author=identity, content="hi", visibility=Post.Visibilities.followers
    )

    sent: dict = {}

    def fake_signed_request(self, method, uri, body=None):
        sent["body"] = body
        return None

    monkeypatch.setattr(Identity, "signed_request", fake_signed_request)

    Post.handle_quote_request_ap(
        {
            "type": "QuoteRequest",
            "id": "https://remote.test/users/quoter/quote-request/2",
            "actor": remote_identity.actor_uri,
            "object": post.object_uri,
            "instrument": "https://remote.test/users/quoter/statuses/43",
        }
    )

    assert not QuoteAuthorization.objects.filter(target_post=post).exists()
    assert sent["body"]["type"] == "Reject"
