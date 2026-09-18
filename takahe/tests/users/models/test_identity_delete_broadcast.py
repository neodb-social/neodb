import datetime

import httpx
import pytest
from django.conf import settings
from django.utils import timezone

from activities.models import FanOut, FanOutStates, Post
from stator.exceptions import TryAgainLater
from users.models import Domain, Follow, Identity, IdentityStates
from users.models.domain import DomainStates
from users.services.delete_broadcast import (
    DeleteBroadcaster,
    identity_broadcast_lock,
)


def make_serializable(identity):
    """The identity fixture has a keypair but no key id, which to_ap() needs."""
    identity.public_key_id = f"{identity.actor_uri}#main-key"
    identity.save()
    return identity


def make_peer(host: str, domain_state: str = "updated", blocked: bool = False):
    domain = Domain.objects.create(
        domain=host, local=False, state=domain_state, blocked=blocked
    )
    return Identity.objects.create(
        actor_uri=f"https://{host}/test-actor/",
        inbox_uri=f"https://{host}/@test/inbox/",
        shared_inbox_uri=f"https://{host}/inbox/",
        username="test",
        domain=domain,
        local=False,
        state="updated",
    )


@pytest.mark.django_db
def test_acquainted_peers_covers_followers_and_leaves_strangers(identity):
    follower = make_peer("follower.test")
    followee = make_peer("followee.test")
    stranger = make_peer("stranger.test")
    Follow.objects.create(source=follower, target=identity, state="accepted")
    Follow.objects.create(source=identity, target=followee, state="accepted")

    acquainted = set(IdentityStates.acquainted_peers(identity))

    assert acquainted == {follower, followee}
    assert stranger not in acquainted


@pytest.mark.django_db
def test_unacquainted_inboxes_skip_the_acquainted_and_the_unreachable(identity):
    follower = make_peer("follower.test")
    Follow.objects.create(source=follower, target=identity, state="accepted")
    stranger = make_peer("stranger.test")
    make_peer("dead.test", domain_state=DomainStates.connection_issue)
    make_peer("blocked.test", blocked=True)

    inboxes = IdentityStates.unacquainted_peer_inboxes(identity)

    assert inboxes == [stranger.shared_inbox_uri]


@pytest.mark.django_db
def test_one_fanout_per_acquainted_peer_sharing_an_inbox(identity):
    first = make_peer("shared-a.test")
    second = make_peer("shared-b.test")
    second.shared_inbox_uri = first.shared_inbox_uri
    second.save()
    Follow.objects.create(source=first, target=identity, state="accepted")
    Follow.objects.create(source=second, target=identity, state="accepted")

    IdentityStates.targets_fan_out(
        identity, FanOut.Types.identity_deleted, broadcast=True
    )

    fan_outs = FanOut.objects.filter(subject_identity=identity)
    assert fan_outs.count() == 1
    # bulk_create bypasses save(), so the state default has to come from the
    # field itself or nothing would ever pick these up.
    assert fan_outs.first().state == FanOutStates.initial_state.name


@pytest.mark.django_db
def test_handle_deleted_hands_over_to_the_broadcast_state(identity, config_system):
    assert (
        IdentityStates.handle_deleted(identity) == IdentityStates.deleted_broadcasting
    )


@pytest.mark.django_db
def test_broadcast_state_sends_to_the_unacquainted_and_finishes(
    identity, config_system, monkeypatch
):
    stranger = make_peer("stranger.test")
    sent = []
    monkeypatch.setattr(
        "users.services.delete_broadcast.broadcast_identity_deletion",
        lambda instance, uris: sent.append((instance, uris)) or (len(uris), len(uris)),
    )

    result = IdentityStates.handle_deleted_broadcasting(identity)

    assert result == IdentityStates.deleted_fanned_out
    assert sent == [(identity, [stranger.shared_inbox_uri])]


@pytest.mark.django_db
def test_broadcast_state_stands_down_when_another_replica_holds_the_lock(
    identity, config_system, monkeypatch
):
    from contextlib import contextmanager

    @contextmanager
    def taken(_pk):
        yield False

    called = []
    monkeypatch.setattr(
        "users.services.delete_broadcast.identity_broadcast_lock", taken
    )
    monkeypatch.setattr(
        "users.services.delete_broadcast.broadcast_identity_deletion",
        lambda instance, uris: called.append(uris),
    )

    result = IdentityStates.handle_deleted_broadcasting(identity)

    assert result == IdentityStates.deleted_fanned_out
    assert called == []


@pytest.mark.django_db
def test_advisory_lock_is_taken_and_released(identity):
    with identity_broadcast_lock(identity.pk) as acquired:
        assert acquired
    # Releasing means the same key can be taken again straight away.
    with identity_broadcast_lock(identity.pk) as acquired:
        assert acquired


@pytest.fixture
def federating(monkeypatch):
    monkeypatch.setattr(settings.SETUP, "NO_FEDERATION", False)


@pytest.mark.django_db
def test_broadcaster_signs_per_inbox_from_one_body_and_swallows_failures(
    identity, config_system, federating
):
    make_serializable(identity)
    identity.deleted = timezone.now()
    seen = []

    def handler(request):
        seen.append(request)
        if "bad.test" in str(request.url):
            raise httpx.ConnectError("nope")
        return httpx.Response(202)

    broadcaster = DeleteBroadcaster(
        identity, concurrency=4, transport=httpx.MockTransport(handler)
    )

    attempted, delivered = broadcaster.send(
        ["https://good.test/inbox/", "https://bad.test/inbox/"]
    )

    assert attempted == 2
    assert delivered == 1
    assert {r.headers["host"] for r in seen} == {"good.test", "bad.test"}
    assert all("keyId=" in r.headers["signature"] for r in seen)
    # One body and one digest, whatever the inbox count.
    assert len({r.headers["digest"] for r in seen}) == 1


@pytest.mark.django_db
def test_broadcaster_stops_dispatching_at_its_deadline(
    identity, config_system, federating
):
    make_serializable(identity)
    identity.deleted = timezone.now()
    seen = []
    broadcaster = DeleteBroadcaster(
        identity,
        deadline=0.0,
        concurrency=2,
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(202)
        ),
    )

    attempted, delivered = broadcaster.send(["https://a.test/inbox/"] * 5)

    assert (attempted, delivered) == (0, 0)
    assert seen == []


@pytest.mark.django_db
def test_identity_deleted_fanout_gives_up_after_a_day(
    identity, remote_identity, config_system, monkeypatch
):
    def refuse(cls, sender, instance, body):
        raise TryAgainLater()

    make_serializable(identity)
    monkeypatch.setattr(FanOutStates, "_deliver", classmethod(refuse))
    fan_out = FanOut.objects.create(
        identity=remote_identity,
        type=FanOut.Types.identity_deleted,
        subject_identity=identity,
    )

    # Still young: the peer may yet come back, so the retry stands.
    with pytest.raises(TryAgainLater):
        FanOutStates.handle_new(fan_out)

    FanOut.objects.filter(pk=fan_out.pk).update(
        state_changed=timezone.now() - datetime.timedelta(days=2)
    )
    fan_out.refresh_from_db()

    assert FanOutStates.handle_new(fan_out) == FanOutStates.failed


@pytest.mark.django_db
def test_other_fanout_types_keep_their_three_day_retry(
    identity, remote_identity, config_system, monkeypatch
):
    def refuse(cls, sender, instance, body):
        raise TryAgainLater()

    monkeypatch.setattr(FanOutStates, "_deliver", classmethod(refuse))
    post = Post.create_local(author=identity, content="hello")
    fan_out = FanOut.objects.create(
        identity=remote_identity,
        type=FanOut.Types.post,
        subject_post=post,
    )
    FanOut.objects.filter(pk=fan_out.pk).update(
        state_changed=timezone.now() - datetime.timedelta(days=2)
    )
    fan_out.refresh_from_db()

    with pytest.raises(TryAgainLater):
        FanOutStates.handle_new(fan_out)
