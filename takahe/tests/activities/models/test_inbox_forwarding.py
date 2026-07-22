import json

import pytest
from pytest_httpx import HTTPXMock

from activities.models import FanOut, Post
from activities.models.fan_out import FanOutStates
from users.models import Domain, Follow, Identity, InboxMessage
from users.models.inbox_message import InboxMessageStates

REPLY_URI = "https://remote.test/posts/reply/1"


def _remote_follower(local_target, domain_name, shared_inbox=None) -> Identity:
    domain = Domain.objects.create(domain=domain_name, local=False, state="updated")
    follower = Identity.objects.create(
        actor_uri=f"https://{domain_name}/users/f/",
        inbox_uri=f"https://{domain_name}/users/f/inbox/",
        shared_inbox_uri=shared_inbox,
        username="f",
        domain=domain,
        local=False,
        state="updated",
    )
    Follow.objects.create(source=follower, target=local_target, state="accepted")
    return follower


def _reply_from(author, local_post, visibility=Post.Visibilities.public) -> Post:
    return Post.objects.create(
        content="<p>Reply</p>",
        author=author,
        local=False,
        visibility=visibility,
        object_uri=REPLY_URI,
        in_reply_to=local_post.object_uri,
    )


def _create_message(actor_uri, local_post) -> dict:
    return {
        "id": f"{actor_uri}activities/create/1",
        "type": "Create",
        "actor": actor_uri,
        "object": {
            "id": REPLY_URI,
            "type": "Note",
            "content": "<p>Reply</p>",
            "attributedTo": actor_uri,
            "inReplyTo": local_post.object_uri,
            # handle_received processes canonicalised documents, where the
            # Public collection URI is compacted to as:Public
            "to": ["as:Public"],
        },
    }


def _raw(message) -> dict:
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        **message,
        "signature": {
            "type": "RsaSignature2017",
            "creator": message["actor"] + "#main-key",
            "created": "2026-07-01T10:00:00Z",
            "signatureValue": "fake==",
        },
    }


@pytest.mark.django_db
def test_signed_reply_forwarded_to_thread_followers(
    identity, remote_identity, config_system
):
    """
    An LD-signed remote reply to a local post is forwarded to the local
    author's remote followers, except those on the reply's origin server.
    """
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    third_party = _remote_follower(
        identity, "remote2.test", shared_inbox="https://remote2.test/inbox/"
    )
    # Follower on the reply author's own server: must be skipped
    origin_domain = remote_identity.domain
    origin_follower = Identity.objects.create(
        actor_uri="https://remote.test/users/g/",
        inbox_uri="https://remote.test/users/g/inbox/",
        username="g",
        domain=origin_domain,
        local=False,
        state="updated",
    )
    Follow.objects.create(source=origin_follower, target=identity, state="accepted")

    _reply_from(remote_identity, local_post)
    message = _create_message(remote_identity.actor_uri, local_post)
    Post.forward_activity_ap(message, _raw(message))

    forwards = FanOut.objects.filter(type=FanOut.Types.forward)
    assert forwards.count() == 1
    forward = forwards.get()
    assert forward.identity == third_party
    assert forward.subject_document == _raw(message)
    assert forward.subject_post is None


@pytest.mark.django_db
def test_unsigned_reply_not_forwarded(identity, remote_identity, config_system):
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    _remote_follower(identity, "remote2.test")
    _reply_from(remote_identity, local_post)
    message = _create_message(remote_identity.actor_uri, local_post)
    # Raw document without an LD signature must not be forwarded
    Post.forward_activity_ap(message, {**message})
    assert not FanOut.objects.filter(type=FanOut.Types.forward).exists()


@pytest.mark.django_db
def test_private_reply_not_forwarded(identity, remote_identity, config_system):
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    _remote_follower(identity, "remote2.test")
    _reply_from(remote_identity, local_post, visibility=Post.Visibilities.followers)
    message = _create_message(remote_identity.actor_uri, local_post)
    Post.forward_activity_ap(message, _raw(message))
    assert not FanOut.objects.filter(type=FanOut.Types.forward).exists()


@pytest.mark.django_db
def test_reply_to_remote_post_not_forwarded(identity, remote_identity, config_system):
    remote_parent = Post.objects.create(
        content="<p>Remote thread</p>",
        author=remote_identity,
        local=False,
        object_uri="https://remote.test/posts/parent/1",
    )
    _remote_follower(identity, "remote2.test")
    reply = Post.objects.create(
        content="<p>Reply</p>",
        author=remote_identity,
        local=False,
        object_uri=REPLY_URI,
        in_reply_to=remote_parent.object_uri,
    )
    message = _create_message(remote_identity.actor_uri, remote_parent)
    message["object"]["inReplyTo"] = remote_parent.object_uri
    assert reply.in_reply_to == remote_parent.object_uri
    Post.forward_activity_ap(message, _raw(message))
    assert not FanOut.objects.filter(type=FanOut.Types.forward).exists()


@pytest.mark.django_db
def test_actor_mismatch_not_forwarded(
    identity, remote_identity, remote_identity2, config_system
):
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    _remote_follower(identity, "remote3.test")
    _reply_from(remote_identity, local_post)
    # remote_identity2 claims an activity about remote_identity's reply
    message = _create_message(remote_identity2.actor_uri, local_post)
    message["object"]["id"] = REPLY_URI
    Post.forward_activity_ap(message, _raw(message))
    assert not FanOut.objects.filter(type=FanOut.Types.forward).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_forward_fan_out_delivers_raw_document(
    httpx_mock: HTTPXMock, identity, remote_identity, config_system
):
    """
    A forward fan-out re-sends the stored document verbatim (LD signature
    intact), HTTP-signed by the system actor.
    """
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    follower = _remote_follower(
        identity, "remote2.test", shared_inbox="https://remote2.test/inbox/"
    )
    message = _create_message(remote_identity.actor_uri, local_post)
    raw = _raw(message)
    fan_out = FanOut.objects.create(
        identity=follower,
        type=FanOut.Types.forward,
        subject_document=raw,
    )
    httpx_mock.add_response(url="https://remote2.test/inbox/", status_code=202)

    assert FanOutStates.handle_new(fan_out) == FanOutStates.sent
    request = httpx_mock.get_requests()[-1]
    assert str(request.url) == "https://remote2.test/inbox/"
    assert json.loads(request.content) == raw
    assert "Signature" in request.headers
    # Signed by the system actor, not the original author
    assert "/actor/" in request.headers["Signature"]


@pytest.mark.django_db
def test_handle_received_forwards_reply_create(
    identity, remote_identity, config_system
):
    """
    End to end through InboxMessage processing: a stored raw_document on a
    Create leads to forward fan-outs once the reply is ingested.
    """
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    _remote_follower(identity, "remote2.test")
    message = _create_message(remote_identity.actor_uri, local_post)
    inbox_message = InboxMessage.objects.create(
        message=message, raw_document=_raw(message)
    )
    assert (
        InboxMessageStates.handle_received(inbox_message)
        == InboxMessageStates.processed
    )
    assert Post.objects.filter(object_uri=REPLY_URI).exists()
    assert FanOut.objects.filter(type=FanOut.Types.forward).count() == 1


@pytest.mark.django_db
def test_handle_received_forwards_reply_delete_before_removal(
    identity, remote_identity, config_system
):
    """
    A Delete of a forwarded reply is forwarded too, even though our copy
    of the reply is removed in the same processing step.
    """
    local_post = Post.create_local(author=identity, content="<p>Thread</p>")
    _remote_follower(identity, "remote2.test")
    _reply_from(remote_identity, local_post)
    message = {
        "id": f"{remote_identity.actor_uri}activities/delete/1",
        "type": "Delete",
        "actor": remote_identity.actor_uri,
        "object": {"id": REPLY_URI, "type": "Tombstone"},
    }
    inbox_message = InboxMessage.objects.create(
        message=message, raw_document=_raw(message)
    )
    assert (
        InboxMessageStates.handle_received(inbox_message)
        == InboxMessageStates.processed
    )
    assert not Post.objects.filter(object_uri=REPLY_URI).exists()
    forward = FanOut.objects.get(type=FanOut.Types.forward)
    assert forward.subject_document["type"] == "Delete"
