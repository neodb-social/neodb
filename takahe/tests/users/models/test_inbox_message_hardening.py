import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.db import IntegrityError
from django.utils import timezone

from stator.exceptions import TryAgainLater
from users.models import Domain, Follow, Identity, InboxMessage, Relay
from users.models.inbox_message import (
    KEY_FETCH_ATTEMPTS,
    MAX_KEY_FETCH_ATTEMPTS,
    InboxMessageStates,
)
from users.models.relay import RelayStates

ACTOR = "https://keyless.test/actor/"


def _deferred_message(**extra_metadata) -> InboxMessage:
    return InboxMessage.objects.create(
        message={
            "type": "Create",
            "actor": ACTOR,
            "object": {"id": f"{ACTOR}posts/1", "type": "Note", "content": "x"},
        },
        metadata={
            "http_sig": {
                "actor_uri": ACTOR,
                "signature": base64.b64encode(b"sig").decode(),
                "headers_string": "(request-target): post /inbox/",
            },
            **extra_metadata,
        },
    )


@pytest.fixture
def fetch_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    def fake_fetch_actor(self) -> bool:
        calls.append(self.actor_uri)
        return False

    monkeypatch.setattr(Identity, "fetch_actor", fake_fetch_actor)
    return calls


@pytest.mark.django_db
def test_deferred_key_fetch_gives_up(fetch_calls):
    """An unknown keyId buys a few key fetches, not one per retry for days."""
    msg = _deferred_message()
    for attempt in range(1, MAX_KEY_FETCH_ATTEMPTS):
        assert InboxMessageStates._verify_deferred(msg) is None
        msg.refresh_from_db()
        assert msg.metadata[KEY_FETCH_ATTEMPTS] == attempt
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.errored
    assert len(fetch_calls) == MAX_KEY_FETCH_ATTEMPTS


@pytest.mark.django_db
def test_deferred_key_fetch_timeout_is_counted(monkeypatch):
    def timing_out(self) -> bool:
        raise TryAgainLater()

    monkeypatch.setattr(Identity, "fetch_actor", timing_out)
    msg = _deferred_message()
    assert InboxMessageStates._verify_deferred(msg) is None
    msg.refresh_from_db()
    assert msg.metadata[KEY_FETCH_ATTEMPTS] == 1


@pytest.mark.django_db
def test_deferred_key_not_refetched_for_fresh_identity(fetch_calls):
    Identity.objects.create(
        actor_uri=ACTOR,
        local=False,
        fetched=timezone.now(),
        domain=Domain.get_remote_domain("keyless.test"),
        username="keyless",
    )
    msg = _deferred_message()
    assert InboxMessageStates._verify_deferred(msg) is None
    assert fetch_calls == []


@pytest.mark.django_db
def test_deferred_relay_sig_with_attempt_counter_still_resolves(keypair):
    """
    The attempt counter shares the metadata dict with the signatures, so it
    must not read as a signature still pending once the relay one is checked.
    """
    relay_uri = "https://relay.test/actor"
    Identity.objects.create(
        actor_uri=relay_uri,
        local=False,
        username="relay",
        domain=Domain.get_remote_domain("relay.test"),
        public_key=keypair["public_key"],
    )
    cleartext = "(request-target): post /inbox/\nhost: example.com"
    private_key = serialization.load_pem_private_key(
        keypair["private_key"].encode(), password=None
    )
    signature = private_key.sign(
        cleartext.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    msg = InboxMessage.objects.create(
        message={"type": "Create", "actor": ACTOR, "object": {"type": "Note"}},
        metadata={
            "relay_http_sig": {
                "relay_uri": relay_uri,
                "signature": base64.b64encode(signature).decode(),
                "headers_string": cleartext,
            },
            KEY_FETCH_ATTEMPTS: 2,
        },
    )
    assert InboxMessageStates._verify_deferred(msg) is True


def _relay_accept(relay: Relay, actor: str) -> InboxMessage:
    return InboxMessage(
        message={
            "type": "Accept",
            "actor": actor,
            "object": {
                "type": "Follow",
                "id": f"https://example.com/actor/relay/{relay.pk}/#follow",
            },
        }
    )


@pytest.mark.django_db
def test_relay_accept_from_another_host_is_refused():
    relay = Relay.objects.create(inbox_uri="https://relay.test/inbox")
    relay.transition_perform(RelayStates.subscribing)
    msg = _relay_accept(relay, "https://evil.test/actor")
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.errored
    relay.refresh_from_db()
    assert relay.state == "subscribing"


@pytest.mark.django_db
def test_relay_accept_from_relay_host_subscribes():
    relay = Relay.objects.create(inbox_uri="https://relay.test/inbox")
    relay.transition_perform(RelayStates.subscribing)
    msg = _relay_accept(relay, "https://relay.test/actor")
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.processed
    relay.refresh_from_db()
    assert relay.state == "subscribed"


@pytest.mark.parametrize("uri", ["abc", "https://remote.test/follow/abc", "/"])
def test_follow_by_ap_rejects_malformed_uri(uri):
    with pytest.raises(Follow.DoesNotExist):
        Follow.by_ap(uri)


@pytest.mark.django_db
@pytest.mark.parametrize("obj", ["abc", "https://remote.test/follow/abc"])
def test_accept_with_malformed_follow_uri_is_not_retried(obj):
    msg = InboxMessage(
        message={"type": "Accept", "actor": "https://remote.test/actor", "object": obj}
    )
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.processed


@pytest.mark.django_db
def test_delete_without_object_id_is_errored(remote_identity):
    msg = InboxMessage(
        message={
            "type": "Delete",
            "actor": remote_identity.actor_uri,
            "object": {"type": "Note"},
        }
    )
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.errored


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error", [KeyError("x"), IndexError(), TypeError(), LookupError(), ValueError()]
)
def test_malformed_payload_errors_are_permanent(monkeypatch, error):
    def handler(data) -> None:
        raise error

    monkeypatch.setattr(Follow, "handle_request_ap", handler)
    msg = InboxMessage(
        message={"type": "Follow", "actor": "https://remote.test/a", "object": "x"}
    )
    assert InboxMessageStates.handle_received(msg) == InboxMessageStates.errored


@pytest.mark.django_db
def test_integrity_error_is_left_to_retry(monkeypatch):
    """A unique-constraint race between workers can pass on the next try."""

    def handler(data) -> None:
        raise IntegrityError("duplicate key")

    monkeypatch.setattr(Follow, "handle_request_ap", handler)
    msg = InboxMessage(
        message={"type": "Follow", "actor": "https://remote.test/a", "object": "x"}
    )
    with pytest.raises(IntegrityError):
        InboxMessageStates.handle_received(msg)
