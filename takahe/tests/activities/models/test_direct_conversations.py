"""
Group direct messages: the wire shape Mastodon, GoToSocial and Pixelfed
exchange (every recipient in `to` with a Mention tag, `inReplyTo` chained,
one `context`/`conversation` URI), and signed fetches of private posts.
"""

import json

import pytest
from django.test import Client
from pytest_httpx import HTTPXMock

from activities.models import Post
from activities.models.conversation import Conversation
from activities.services import PostService
from core.ld import canonicalise
from core.signatures import HttpSignature
from users.models import Block, Domain, Identity

AP_ACCEPT = "application/activity+json,application/ld+json"


@pytest.fixture
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


@pytest.fixture
def signing_remote(remote_identity: Identity, keypair) -> Identity:
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = remote_identity.actor_uri + "#main-key"
    remote_identity.save()
    return remote_identity


def _remote(domain: str, username: str = "u", key: str | None = None) -> Identity:
    domain_obj, _ = Domain.objects.get_or_create(
        domain=domain, defaults={"local": False, "state": "updated"}
    )
    return Identity.objects.create(
        actor_uri=f"https://{domain}/users/{username}",
        inbox_uri=f"https://{domain}/users/{username}/inbox",
        profile_uri=f"https://{domain}/@{username}",
        username=username,
        domain=domain_obj,
        local=False,
        state="updated",
        public_key=key,
        public_key_id=f"https://{domain}/users/{username}#main-key" if key else None,
    )


def _signed_get(
    httpx_mock: HTTPXMock, path: str, private_key: str, key_id: str
) -> dict:
    httpx_mock.add_response()
    HttpSignature.signed_request(
        uri=f"https://example.com{path}",
        body=None,
        private_key=private_key,
        key_id=key_id,
        method="get",
    )
    sent = httpx_mock.get_requests()[-1]
    return {
        "HTTP_ACCEPT": sent.headers["accept"],
        "HTTP_DATE": sent.headers["date"],
        "HTTP_SIGNATURE": sent.headers["signature"],
    }


def _note(author: Identity, to: list[Identity], n: int, **extra) -> dict:
    """An inbound direct Note, in canonicalised vocabulary"""
    return {
        "id": f"{author.actor_uri}/statuses/{n}",
        "type": "Note",
        "attributedTo": author.actor_uri,
        "to": [i.actor_uri for i in to],
        "content": f"<p>message {n}</p>",
        "published": f"2026-10-0{n}T10:00:00Z",
        "tag": [
            {"type": "Mention", "href": i.actor_uri, "name": f"@{i.username}"}
            for i in to
        ],
        **extra,
    }


def _post_path(post: Post) -> str:
    author = post.author
    return f"/@{author.username}@{author.domain_id}/posts/{post.pk}/"


@pytest.mark.django_db
def test_outbound_group_dm_shape(
    identity: Identity,
    other_identity: Identity,
    remote_identity: Identity,
    config_system,
):
    post = Post.create_local(
        author=identity,
        content="@other@example.com @test@remote.test hello both",
        visibility=Post.Visibilities.mentioned,
    )
    post = Post.objects.get(pk=post.pk)
    conversation = post.conversation
    assert conversation is not None
    assert conversation.uri == f"{identity.actor_uri}conversations/{conversation.pk}/"

    activity = post.to_create_ap()
    note = activity["object"]
    recipients = {other_identity.actor_uri, remote_identity.actor_uri}
    assert set(note["to"]) == recipients
    assert set(activity["to"]) == recipients
    assert "cc" not in note
    assert {t["href"] for t in note["tag"] if t["type"] == "Mention"} == recipients
    assert note["context"] == note["conversation"] == conversation.uri
    assert activity["directMessage"] is True

    document = canonicalise(post.to_create_ap())
    context_terms = document["@context"][1]
    assert context_terms["conversation"]["@id"] == "ostatus:conversation"
    assert context_terms["directMessage"] == "litepub:directMessage"
    assert document["directMessage"] is True
    assert document["object"]["conversation"] == conversation.uri
    assert document["object"]["context"] == conversation.uri


@pytest.mark.django_db
def test_outbound_public_post_has_no_dm_fields(
    identity: Identity, remote_identity: Identity, config_system
):
    post = Post.create_local(
        author=identity,
        content="@test@remote.test hello",
        visibility=Post.Visibilities.public,
    )
    post = Post.objects.get(pk=post.pk)
    activity = post.to_create_ap()
    assert "directMessage" not in activity
    assert "context" not in activity["object"]
    assert activity["object"]["cc"] == [remote_identity.actor_uri]


@pytest.mark.django_db
def test_inbound_mastodon_group_dm(identity: Identity, config_system):
    alice = _remote("mastodon.test", "alice")
    bob = _remote("gts.test", "bob")
    post = Post.by_ap(
        _note(
            alice,
            [identity, bob],
            1,
            context="https://mastodon.test/contexts/1-2",
            conversation="tag:mastodon.test,2026-10-01:objectId=2:objectType=Conversation",
        ),
        create=True,
    )
    conversation = post.conversation
    assert post.visibility == Post.Visibilities.mentioned
    assert set(conversation.participants.all()) == {alice, bob, identity}
    # Mastodon threads root statuses by `conversation`, so that one is echoed
    assert conversation.uri.startswith("tag:mastodon.test")


@pytest.mark.django_db
def test_inbound_pixelfed_thread_keeps_first_uri(identity: Identity, config_system):
    carol = _remote("pixelfed.test", "carol")
    first = Post.by_ap(
        _note(
            carol,
            [identity],
            1,
            context="https://pixelfed.test/i/dm/contexts/7",
            conversation="https://pixelfed.test/i/dm/contexts/7",
        ),
        create=True,
    )
    second = Post.by_ap(
        _note(
            carol,
            [identity],
            2,
            inReplyTo=first.object_uri,
            context="https://pixelfed.test/i/dm/contexts/8",
        ),
        create=True,
    )
    assert first.conversation_id == second.conversation_id
    conversation = Conversation.objects.get(pk=first.conversation_id)
    assert conversation.uri == "https://pixelfed.test/i/dm/contexts/7"


@pytest.mark.django_db
def test_inbound_gotosocial_without_context_then_local_reply(
    identity: Identity, config_system
):
    dave = _remote("gts.test", "dave")
    inbound = Post.by_ap(_note(dave, [identity], 1), create=True)
    assert inbound.conversation.uri is None

    reply = Post.create_local(
        author=identity,
        content="hi back",
        visibility=Post.Visibilities.mentioned,
        reply_to=Post.objects.get(pk=inbound.pk),
    )
    conversation = Conversation.objects.get(pk=inbound.conversation_id)
    assert reply.conversation_id == conversation.pk
    assert conversation.uri == f"{identity.actor_uri}conversations/{conversation.pk}/"
    note = Post.objects.get(pk=reply.pk).to_ap()
    assert note["inReplyTo"] == inbound.object_uri
    assert note["context"] == conversation.uri


@pytest.mark.django_db
def test_inbound_reply_that_drops_a_mention_splits(identity: Identity, config_system):
    """Every peer keys conversations by participant set; so do we."""
    erin = _remote("mastodon.test", "erin")
    frank = _remote("gts.test", "frank")
    uri = "https://mastodon.test/contexts/9"
    group = Post.by_ap(_note(erin, [identity, frank], 1, context=uri), create=True)
    pair = Post.by_ap(
        _note(erin, [identity], 2, inReplyTo=group.object_uri, context=uri),
        create=True,
    )
    assert group.conversation_id != pair.conversation_id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "https://x.test/" + "a" * 600, {"type": "Collection"}],
)
def test_inbound_unusable_conversation_uri_is_ignored(
    identity: Identity, config_system, value
):
    gina = _remote("mastodon.test", "gina")
    post = Post.by_ap(_note(gina, [identity], 1, conversation=value), create=True)
    assert post.conversation.uri is None


@pytest.mark.django_db
def test_author_reply_marks_own_membership_read(identity: Identity, config_system):
    hank = _remote("mastodon.test", "hank")
    inbound = Post.by_ap(_note(hank, [identity], 1), create=True)
    membership = inbound.conversation.memberships.get(identity=identity)
    assert membership.unread is True
    Post.create_local(
        author=identity,
        content="reply",
        visibility=Post.Visibilities.mentioned,
        reply_to=Post.objects.get(pk=inbound.pk),
    )
    membership.refresh_from_db()
    assert membership.unread is False


@pytest.mark.django_db
def test_context_hides_direct_ancestor_from_outsiders(
    identity: Identity,
    other_identity: Identity,
    remote_identity: Identity,
    config_system,
):
    dm = Post.create_local(
        author=identity,
        content="@test@remote.test secret",
        visibility=Post.Visibilities.mentioned,
    )
    public_reply = Post.create_local(
        author=identity,
        content="public follow-up",
        visibility=Post.Visibilities.public,
        reply_to=Post.objects.get(pk=dm.pk),
    )
    service = PostService(Post.objects.get(pk=public_reply.pk))
    assert service.context(identity=None)[0] == []
    assert service.context(identity=other_identity)[0] == []
    assert [p.pk for p in service.context(identity=remote_identity)[0]] == [dm.pk]
    assert [p.pk for p in service.context(identity=identity)[0]] == [dm.pk]


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_signed_fetch_of_direct_post(
    httpx_mock: HTTPXMock,
    identity: Identity,
    signing_remote: Identity,
    keypair,
    config_system,
):
    dm = Post.create_local(
        author=identity,
        content="@test@remote.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    path = _post_path(dm)
    client = Client(HTTP_HOST="example.com")

    assert client.get(path, HTTP_ACCEPT=AP_ACCEPT).status_code == 404

    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], signing_remote.public_key_id
    )
    response = client.get(path, **headers)
    assert response.status_code == 200
    assert "private" in response.headers["cache-control"]
    body = json.loads(response.content)
    assert body["id"] == dm.object_uri
    assert body["to"] == signing_remote.actor_uri

    # Nothing was cached for the next, unsigned, requester
    assert client.get(path, HTTP_ACCEPT=AP_ACCEPT).status_code == 404


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_signed_fetch_by_participant_server_actor(
    httpx_mock: HTTPXMock, identity: Identity, keypair, config_system
):
    """Mastodon fetches a missing parent signed by its instance actor."""
    alice = _remote("mastodon.test", "alice")
    instance_actor = _remote(
        "mastodon.test", "mastodon.test", key=keypair["public_key"]
    )
    dm = Post.create_local(
        author=identity,
        content="@alice@mastodon.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    assert set(dm.mentions.all()) == {alice}
    path = _post_path(dm)
    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], instance_actor.public_key_id
    )
    assert Client(HTTP_HOST="example.com").get(path, **headers).status_code == 200


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_signed_fetch_by_unknown_instance_actor(
    httpx_mock: HTTPXMock, identity: Identity, keypair, config_system
):
    """A participant server's actor we have never seen is fetched, then trusted."""
    _remote("mastodon.test", "alice")
    dm = Post.create_local(
        author=identity,
        content="@alice@mastodon.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    path = _post_path(dm)
    actor_uri = "https://mastodon.test/actor"
    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], actor_uri + "#main-key"
    )
    httpx_mock.add_response(
        url=actor_uri,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                "https://w3id.org/security/v1",
            ],
            "id": actor_uri,
            "type": "Application",
            "preferredUsername": "mastodon.test",
            "inbox": actor_uri + "/inbox",
            "publicKey": {
                "id": actor_uri + "#main-key",
                "owner": actor_uri,
                "publicKeyPem": keypair["public_key"],
            },
        },
    )
    httpx_mock.add_response(
        url="https://mastodon.test/.well-known/webfinger?resource=acct:mastodon.test@mastodon.test",
        status_code=404,
    )
    httpx_mock.add_response(
        url="https://mastodon.test/.well-known/host-meta", status_code=404
    )
    client = Client(HTTP_HOST="example.com")
    assert client.get(path, **headers).status_code == 200
    assert Identity.objects.filter(actor_uri=actor_uri).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_signed_fetch_by_unknown_outsider_is_not_fetched(
    httpx_mock: HTTPXMock, identity: Identity, keypair, config_system
):
    _remote("mastodon.test", "alice")
    dm = Post.create_local(
        author=identity,
        content="@alice@mastodon.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    path = _post_path(dm)
    headers = _signed_get(
        httpx_mock,
        path,
        keypair["private_key"],
        "https://elsewhere.test/actor#main-key",
    )
    assert Client(HTTP_HOST="example.com").get(path, **headers).status_code == 404
    assert not Identity.objects.filter(
        actor_uri__startswith="https://elsewhere"
    ).exists()
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_signed_fetch_refused(
    httpx_mock: HTTPXMock,
    identity: Identity,
    signing_remote: Identity,
    keypair,
    config_system,
):
    outsider = _remote("elsewhere.test", "eve", key=keypair["public_key"])
    _remote("mastodon.test", "alice")
    dm = Post.create_local(
        author=identity,
        content="@alice@mastodon.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    path = _post_path(dm)
    client = Client(HTTP_HOST="example.com")

    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], outsider.public_key_id
    )
    assert client.get(path, **headers).status_code == 404

    # A participant the author blocks is refused too
    blocked_dm = Post.create_local(
        author=identity,
        content="@test@remote.test hi",
        visibility=Post.Visibilities.mentioned,
    )
    Block.create_local_block(identity, signing_remote)
    path = _post_path(blocked_dm)
    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], signing_remote.public_key_id
    )
    assert client.get(path, **headers).status_code == 404

    # A bad signature is refused
    headers["HTTP_SIGNATURE"] = headers["HTTP_SIGNATURE"].replace(
        'signature="', 'signature="AAAA'
    )
    assert client.get(path, **headers).status_code == 404


@pytest.mark.django_db
@pytest.mark.usefixtures("_enable_federation")
def test_conversation_collection(
    httpx_mock: HTTPXMock,
    identity: Identity,
    other_identity: Identity,
    signing_remote: Identity,
    keypair,
    config_system,
):
    first = Post.create_local(
        author=identity,
        content="@test@remote.test one",
        visibility=Post.Visibilities.mentioned,
    )
    second = Post.create_local(
        author=identity,
        content="@test@remote.test two",
        visibility=Post.Visibilities.mentioned,
        reply_to=Post.objects.get(pk=first.pk),
    )
    conversation = Conversation.objects.get(pk=first.conversation_id)
    path = f"/@test@example.com/conversations/{conversation.pk}/"
    assert conversation.uri == f"https://example.com{path}"
    client = Client(HTTP_HOST="example.com")

    assert client.get(path, HTTP_ACCEPT=AP_ACCEPT).status_code == 404

    headers = _signed_get(
        httpx_mock, path, keypair["private_key"], signing_remote.public_key_id
    )
    response = client.get(path, **headers)
    assert response.status_code == 200
    assert "private" in response.headers["cache-control"]
    body = json.loads(response.content)
    assert body["id"] == conversation.uri
    assert body["type"] == "OrderedCollection"
    assert body["totalItems"] == 2
    assert body["orderedItems"] == [
        Post.objects.get(pk=first.pk).object_uri,
        Post.objects.get(pk=second.pk).object_uri,
    ]

    # The URI only resolves under the handle that minted it
    other_path = f"/@other@example.com/conversations/{conversation.pk}/"
    headers = _signed_get(
        httpx_mock, other_path, keypair["private_key"], signing_remote.public_key_id
    )
    assert client.get(other_path, **headers).status_code == 404
