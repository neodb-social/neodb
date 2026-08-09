import base64
import json
from email.utils import format_datetime

import pytest
from core.ld import canonicalise
from core.signatures import HttpSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.test import Client
from django.utils import timezone

from users.models import (
    FeatureAuthorization,
    Identity,
    InboxMessage,
    InboxMessageStates,
)

COLLECTION_URI = "https://remote.test/users/curator/collections/23"
REQUEST_URI = "https://remote.test/users/curator/feature_requests/1"


@pytest.fixture(autouse=True)
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


def _feature_request(object_uri: str, actor_uri: str | None = None) -> dict:
    """
    A FeatureRequest as it arrives: Mastodon's FeatureRequestSerializer emits no
    `actor` at all, so `actor_uri` here stands for what the inbox fills in from
    the signature before handing the message on.
    """
    request = {
        "type": "FeatureRequest",
        "id": REQUEST_URI,
        "object": object_uri,
        "instrument": COLLECTION_URI,
    }
    if actor_uri:
        request["actor"] = actor_uri
    return request


### Actor policy ###


@pytest.mark.django_db
def test_to_ap_allows_featuring_when_discoverable(identity: Identity, config_system):
    """A discoverable identity consents to being featured by anyone."""
    identity.discoverable = True
    ap = identity.to_ap()
    assert ap["interactionPolicy"]["canFeature"]["automaticApproval"] == ["as:Public"]


@pytest.mark.django_db
def test_to_ap_refuses_featuring_when_not_discoverable(
    identity: Identity, config_system
):
    """
    Discovery off is an explicit refusal, spelled as the actor's own id since
    FEP-7aa9 treats an absent policy as missing consent too, but an empty list
    would not survive JSON-LD canonicalisation.
    """
    identity.discoverable = False
    ap = identity.to_ap()
    assert ap["interactionPolicy"]["canFeature"]["automaticApproval"] == [
        identity.actor_uri
    ]


@pytest.mark.django_db
def test_to_ap_omits_policy_for_remote_identity(
    remote_identity: Identity, config_system
):
    """We only speak for our own users."""
    assert "interactionPolicy" not in remote_identity.to_ap()


@pytest.mark.django_db
def test_policy_survives_canonicalisation(identity: Identity, config_system):
    """
    The terms are not declared in our outbound context, so they only reach the
    wire because the ActivityStreams context maps unknown terms via @vocab.
    Mastodon reads actors as plain JSON, so the key has to come out unchanged.
    """
    identity.discoverable = True
    # The factory supplies a keypair directly, bypassing generate_keys()
    identity.public_key_id = identity.actor_uri + "#main-key"
    ap = canonicalise(identity.to_ap(), include_security=True)
    assert ap["interactionPolicy"]["canFeature"]["automaticApproval"] == "as:Public"


### Stamp ###


@pytest.mark.django_db
def test_to_ap_shape(identity: Identity, config_system):
    auth = FeatureAuthorization.objects.create(
        identity=identity,
        collection_uri=COLLECTION_URI,
        request_uri=REQUEST_URI,
    )
    ap = auth.to_ap()
    assert ap["type"] == "FeatureAuthorization"
    assert ap["id"] == auth.object_uri
    assert ap["interactingObject"] == COLLECTION_URI
    assert ap["interactionTarget"] == identity.actor_uri


@pytest.mark.django_db
def test_url_is_identity_scoped(identity: Identity, config_system):
    """
    Mastodon rejects a stamp hosted anywhere but the featured actor's domain,
    so it has to hang off the actor URI.
    """
    auth = FeatureAuthorization.objects.create(
        identity=identity, collection_uri=COLLECTION_URI
    )
    assert auth.object_uri.startswith(identity.actor_uri)
    assert auth.object_uri.endswith(f"/feature-auth/{auth.id}/")


@pytest.mark.django_db
def test_view_serves_authorization(identity: Identity, config_system):
    auth = FeatureAuthorization.objects.create(
        identity=identity, collection_uri=COLLECTION_URI
    )
    client = Client(HTTP_HOST="example.com")
    path = f"/@{identity.username}@{identity.domain.domain}/feature-auth/{auth.id}/"
    resp = client.get(path, headers={"accept": "application/activity+json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/activity+json")
    body = json.loads(resp.content)
    assert body["type"] == "FeatureAuthorization"
    assert body["id"].endswith(f"/feature-auth/{auth.id}/")
    assert body["interactingObject"] == COLLECTION_URI
    assert body["interactionTarget"] == identity.actor_uri


@pytest.mark.django_db
def test_view_404_once_discovery_turned_off(identity: Identity, config_system):
    """
    Turning discovery off withdraws consent: the stamp stops resolving, which
    is what makes remote servers drop the entry on re-verification.
    """
    auth = FeatureAuthorization.objects.create(
        identity=identity, collection_uri=COLLECTION_URI
    )
    identity.discoverable = False
    identity.save()
    client = Client(HTTP_HOST="example.com")
    path = f"/@{identity.username}@{identity.domain.domain}/feature-auth/{auth.id}/"
    resp = client.get(path, headers={"accept": "application/activity+json"})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_view_404_for_mismatched_identity(
    identity: Identity, other_identity: Identity, config_system
):
    auth = FeatureAuthorization.objects.create(
        identity=identity, collection_uri=COLLECTION_URI
    )
    client = Client(HTTP_HOST="example.com")
    path = (
        f"/@{other_identity.username}@{other_identity.domain.domain}"
        f"/feature-auth/{auth.id}/"
    )
    resp = client.get(path, headers={"accept": "application/activity+json"})
    assert resp.status_code == 404


### FeatureRequest handling ###


@pytest.mark.django_db
def test_handle_feature_request_accepts_when_discoverable(
    monkeypatch, identity: Identity, remote_identity: Identity, config_system
):
    identity.discoverable = True
    identity.save()

    sent: dict = {}

    def fake_signed_request(self, method, uri, body=None):
        sent["uri"] = uri
        sent["body"] = body
        return None

    monkeypatch.setattr(Identity, "signed_request", fake_signed_request)

    Identity.handle_feature_request_ap(
        _feature_request(identity.actor_uri, actor_uri=remote_identity.actor_uri)
    )

    auth = FeatureAuthorization.objects.get(identity=identity)
    assert auth.collection_uri == COLLECTION_URI
    assert auth.request_uri == REQUEST_URI

    assert sent["uri"] == remote_identity.inbox_uri
    accept = sent["body"]
    assert accept["type"] == "Accept"
    # Mastodon looks the pending item up by the FeatureRequest id
    assert accept["object"] == REQUEST_URI
    result = accept["result"]
    assert result["type"] == "FeatureAuthorization"
    assert result["id"] == auth.object_uri
    assert "#" not in result["id"]
    assert result["interactingObject"] == COLLECTION_URI
    assert result["interactionTarget"] == identity.actor_uri


@pytest.mark.django_db
def test_handle_feature_request_rejects_when_not_discoverable(
    monkeypatch, identity: Identity, remote_identity: Identity, config_system
):
    identity.discoverable = False
    identity.save()

    sent: dict = {}

    def fake_signed_request(self, method, uri, body=None):
        sent["body"] = body
        return None

    monkeypatch.setattr(Identity, "signed_request", fake_signed_request)

    Identity.handle_feature_request_ap(
        _feature_request(identity.actor_uri, actor_uri=remote_identity.actor_uri)
    )

    assert not FeatureAuthorization.objects.filter(identity=identity).exists()
    assert sent["body"]["type"] == "Reject"
    assert sent["body"]["object"] == REQUEST_URI


@pytest.mark.django_db
def test_handle_feature_request_ignores_remote_target(
    monkeypatch, remote_identity: Identity, remote_identity2: Identity, config_system
):
    """We never answer on behalf of an identity that is not ours."""
    calls: list = []
    monkeypatch.setattr(
        Identity,
        "signed_request",
        lambda self, method, uri, body=None: calls.append(uri),
    )

    Identity.handle_feature_request_ap(
        _feature_request(
            remote_identity.actor_uri, actor_uri=remote_identity2.actor_uri
        )
    )

    assert not FeatureAuthorization.objects.exists()
    assert calls == []


@pytest.mark.django_db
def test_inbox_routes_feature_request(
    monkeypatch, identity: Identity, remote_identity: Identity, config_system
):
    """
    The whole complaint was that these fell through to `errored` and were
    silently dropped, leaving the remote entry pending forever.
    """
    monkeypatch.setattr(
        Identity, "signed_request", lambda self, method, uri, body=None: None
    )
    message = InboxMessage.objects.create(
        message=_feature_request(
            identity.actor_uri, actor_uri=remote_identity.actor_uri
        )
    )
    assert InboxMessageStates.handle_received(message) == InboxMessageStates.processed
    assert FeatureAuthorization.objects.filter(identity=identity).exists()


### Inbox delivery ###


def _sign_and_post(client, identity: Identity, document: dict, keypair, key_id: str):
    """POST document to identity's inbox, HTTP-signed by key_id."""
    body = json.dumps(document).encode()
    path = identity.inbox_uri.replace("https://example.com", "")
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)
    headers_to_sign = ["(request-target)", "host", "date", "digest", "content-type"]
    headers_string = "\n".join(
        f"{h}: {v}"
        for h, v in [
            ("(request-target)", f"post {path}"),
            ("host", "example.com"),
            ("date", date_str),
            ("digest", digest),
            ("content-type", "application/activity+json"),
        ]
    )
    private_key = serialization.load_pem_private_key(
        keypair["private_key"].encode(), password=None
    )
    signature = base64.b64encode(
        private_key.sign(headers_string.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return client.post(
        identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=(
            f'keyId="{key_id}",'
            f'headers="{" ".join(headers_to_sign)}",'
            f'signature="{signature}",'
            f'algorithm="rsa-sha256"'
        ),
    )


@pytest.mark.django_db
def test_inbox_takes_requester_from_signature(
    monkeypatch,
    client,
    identity: Identity,
    remote_identity: Identity,
    keypair,
    config_system,
):
    """
    The actual delivery carries no `actor`, so the inbox used to answer 400
    "Unspecified actor" before storing anything -- and Mastodon treats 400 as
    permanent, so nothing was ever retried. The requester is the signer.
    """
    identity.discoverable = True
    identity.save()
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()

    sent: dict = {}
    monkeypatch.setattr(
        Identity,
        "signed_request",
        lambda self, method, uri, body=None: sent.update(uri=uri, body=body),
    )

    document = _feature_request(identity.actor_uri)
    assert "actor" not in document
    resp = _sign_and_post(client, identity, document, keypair, key_id)

    assert resp.status_code == 202
    message = InboxMessage.objects.get()
    assert message.metadata is None
    assert message.message["actor"] == remote_identity.actor_uri

    assert InboxMessageStates.handle_received(message) == InboxMessageStates.processed
    auth = FeatureAuthorization.objects.get(identity=identity)
    assert auth.collection_uri == COLLECTION_URI
    assert auth.request_uri == REQUEST_URI
    assert sent["uri"] == remote_identity.inbox_uri
    assert sent["body"]["type"] == "Accept"


@pytest.mark.django_db
def test_inbox_rejects_unsigned_feature_request(identity: Identity, config_system):
    """Without a signature there is nobody to attribute the request to."""
    client = Client()
    resp = client.post(
        identity.inbox_uri,
        data=json.dumps(_feature_request(identity.actor_uri)),
        content_type="application/activity+json",
        HTTP_HOST="example.com",
    )
    assert resp.status_code == 400
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_inbox_rejects_feature_request_signed_by_a_non_uri_key(
    identity: Identity, keypair, config_system
):
    """
    keyId is attacker-controlled: a parseable but non-URI one must not be
    adopted as the actor, or actor and domain lookup choke on it before any
    signature has been checked.
    """
    client = Client()
    resp = _sign_and_post(
        client, identity, _feature_request(identity.actor_uri), keypair, "not-a-url#key"
    )
    assert resp.status_code == 400
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_inbox_still_rejects_other_activities_without_actor(
    client, identity: Identity, remote_identity: Identity, keypair, config_system
):
    """
    Only the types that are defined without an actor may borrow the signer's:
    everything else keeps failing closed.
    """
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()

    resp = _sign_and_post(
        client,
        identity,
        {
            "type": "Follow",
            "id": f"{remote_identity.actor_uri}activities/follow/1",
            "object": identity.actor_uri,
        },
        keypair,
        key_id,
    )
    assert resp.status_code == 400
    assert InboxMessage.objects.count() == 0
