import pytest
from activities.models import Post
from django.db import connection
from django.test.utils import CaptureQueriesContext

from users.models import InboxMessage


@pytest.mark.django_db
def test_webfinger_actor(client, identity):
    """
    Ensures the webfinger and actor URLs are working properly
    """
    identity.generate_keypair()
    # Fetch their webfinger
    response = client.get("/.well-known/webfinger?resource=acct:test@example.com")
    assert response.headers["content-type"] == "application/jrd+json"
    data = response.json()
    assert data["subject"] == "acct:test@example.com"
    assert data["aliases"][0] == "https://example.com/@test/"
    # Fetch their actor
    data = client.get("/@test@example.com/", HTTP_ACCEPT="application/ld+json").json()
    assert data["id"] == "https://example.com/@test@example.com/"
    assert data["endpoints"]["sharedInbox"] == "https://example.com/inbox/"


@pytest.mark.django_db
def test_webfinger_system_actor(client):
    """
    Ensures the webfinger and actor URLs are working properly for system actor
    """
    # Fetch their webfinger
    data = client.get(
        "/.well-known/webfinger?resource=acct:__system__@example.com"
    ).json()
    assert data["subject"] == "acct:__system__@example.com"
    assert data["aliases"][0] == "https://example.com/about/"
    # Fetch their actor
    data = client.get("/actor/", HTTP_ACCEPT="application/ld+json").json()
    assert data["id"] == "https://example.com/actor/"
    assert data["inbox"] == "https://example.com/actor/inbox/"
    assert data["endpoints"]["sharedInbox"] == "https://example.com/inbox/"


@pytest.mark.django_db
def test_delete_unknown_actor(client, identity):
    """
    Tests that unknown actor delete messages are dropped
    """
    data = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "actor": "https://mastodon.test/users/fakec8b6984105c8f15070a2",
        "id": "https://mastodon.test/users/fakec8b6984105c8f15070a2#delete",
        "object": "https://mastodon.test/users/fakec8b6984105c8f15070a2",
        "signature": {
            "created": "2022-12-06T03:54:28Z",
            "creator": "https://mastodon.test/users/fakec8b6984105c8f15070a2#main-key",
            "signatureValue": "This value doesn't matter",
            "type": "RsaSignature2017",
        },
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "type": "Delete",
    }
    resp = client.post(
        identity.inbox_uri, data=data, content_type="application/activity+json"
    )
    assert resp.status_code == 202


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mangle",
    [
        {"@context": 42},
        {"actor": {"id": {"nested": "bad"}}},
        {"type": {"weird": "object"}},
        {"type": None},
        {"type": "Delete", "object": None},
    ],
    ids=[
        "bad-context",
        "nested-actor-id",
        "non-string-type",
        "no-type",
        "delete-without-object",
    ],
)
def test_malformed_document_rejected(client, identity, mangle):
    """
    Documents that crash JSON-LD processing, name no usable type, or delete
    nothing are rejected with 400 rather than a server error. None values
    mean the key is dropped (canonicalise discards nulls anyway).
    """
    document = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": "https://remote.test/a/activities/1",
        "type": "Create",
        "actor": "https://remote.test/a/",
        "object": {"id": "https://remote.test/a/posts/1", "type": "Note"},
    }
    document.update(mangle)
    document = {k: v for k, v in document.items() if v is not None}
    resp = client.post(
        identity.inbox_uri, data=document, content_type="application/activity+json"
    )
    assert resp.status_code == 400
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_invalid_utf8_body_rejected(client, identity):
    """
    A body that does not decode as UTF-8 is a 400; the error logging must
    not assume it decodes either.
    """
    resp = client.post(
        identity.inbox_uri,
        data=b'{"actor": "\xff"}',
        content_type="application/activity+json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_ignore_lemmy(client, identity):
    """
    Tests that message types we know we cannot handle are ignored immediately
    """
    data = {
        "cc": "https://lemmy.ml/c/asklemmy/followers",
        "id": "https://lemmy.ml/activities/announce/12345",
        "to": "as:Public",
        "type": "Announce",
        "actor": "https://lemmy.ml/c/asklemmy",
        "object": {
            "id": "https://lemmy.world/activities/like/12345",
            "type": "Like",
            "actor": "https://lemmy.world/u/Nobody",
            "object": "https://sopuli.xyz/comment/12345",
            "audience": "https://lemmy.ml/c/asklemmy",
        },
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
            {
                "pt": "https://joinpeertube.org/ns#",
                "sc": "http://schema.org/",
                "lemmy": "https://join-lemmy.org/ns#",
                "expires": "as:endTime",
                "litepub": "http://litepub.social/ns#",
                "language": "sc:inLanguage",
                "stickied": "lemmy:stickied",
                "sensitive": "as:sensitive",
                "identifier": "sc:identifier",
                "moderators": {"@id": "lemmy:moderators", "@type": "@id"},
                "removeData": "lemmy:removeData",
                "ChatMessage": "litepub:ChatMessage",
                "matrixUserId": "lemmy:matrixUserId",
                "distinguished": "lemmy:distinguished",
                "commentsEnabled": "pt:commentsEnabled",
                "postingRestrictedToMods": "lemmy:postingRestrictedToMods",
            },
            "https://w3id.org/security/v1",
        ],
    }
    num_inbox_messages = InboxMessage.objects.count()
    resp = client.post(
        identity.inbox_uri, data=data, content_type="application/activity+json"
    )
    assert num_inbox_messages == InboxMessage.objects.count()
    assert resp.status_code == 202


@pytest.mark.django_db
def test_outbox_bounded_queries(client, identity, other_identity, config_system):
    """
    The outbox serialises many posts: relations must be prefetched and the
    replies collections loaded in one query rather than one per post.
    """
    parent = Post.create_local(identity, "<p>Hello @other@example.com</p>")
    for i in range(4):
        Post.create_local(identity, f"<p>Post {i}</p>")
    reply = Post.create_local(other_identity, "<p>Reply</p>", reply_to=parent)
    Post.create_local(
        other_identity,
        "<p>Private reply</p>",
        visibility=Post.Visibilities.followers,
        reply_to=parent,
    )

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(
            "/@test@example.com/outbox/", HTTP_ACCEPT="application/ld+json"
        )
    assert response.status_code == 200
    data = response.json()
    assert data["totalItems"] == 5
    by_id = {item["id"]: item for item in data["orderedItems"]}
    # canonicalise() compacts a one-item list to a bare string
    assert by_id[parent.object_uri]["replies"]["first"]["items"] == reply.object_uri
    assert "https://example.com/@other@example.com/" in by_id[parent.object_uri]["cc"]

    sql = [q["sql"] for q in ctx.captured_queries]
    for fragment in (
        '"activities_post_mentions"',
        '"activities_post_emojis"',
        '"activities_postattachment"',
        '"in_reply_to" IN',
    ):
        assert sum(fragment in s for s in sql) <= 1, fragment
