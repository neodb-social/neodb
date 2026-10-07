"""The Messages pages: direct conversations between members."""

import pytest
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from social.conversations import message_html
from takahe.models import (
    Conversation,
    ConversationMembership,
    Domain,
    Identity,
    Post,
    TimelineEvent,
)
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def _start(client: Client, to: str, content: str):
    return client.post(
        reverse("social:conversation_new"), {"to": to, "content": content}
    )


def _membership(user: User, conversation_id: int) -> ConversationMembership:
    return ConversationMembership.objects.get(
        identity_id=user.identity.pk, conversation_id=conversation_id
    )


def test_new_group_message_creates_one_conversation():
    alice, client = _member("alice")
    bob, _ = _member("bob")
    carol, _ = _member("carol")

    response = _start(client, "@bob, carol", "hello both")

    post = Post.objects.get(author_id=alice.identity.pk)
    assert post.visibility == Post.Visibilities.mentioned
    assert set(post.mentions.values_list("pk", flat=True)) == {
        bob.identity.pk,
        carol.identity.pk,
    }
    assert "@bob" not in post.content
    conversation = Conversation.objects.get(pk=post.conversation_id)
    assert response.status_code == 302
    assert response.url == reverse("social:conversation", args=[conversation.pk])
    assert _membership(alice, conversation.pk).unread is False
    assert _membership(bob, conversation.pk).unread is True
    assert _membership(carol, conversation.pk).unread is True


def test_new_message_rejects_unknown_and_empty():
    alice, client = _member("alice")

    content = _start(client, "@nobody", "hi").content.decode()
    assert "@nobody was not found." in content

    content = _start(client, "@someone@far.example", "hi").content.decode()
    assert "is not known here yet" in content

    content = _start(client, "@someone@", "hi").content.decode()
    assert "@someone@ was not found." in content

    _member("bob")
    content = _start(client, "@bob", "").content.decode()
    assert "Message cannot be empty." in content

    content = _start(client, "@alice", "talking to myself").content.decode()
    assert "Add at least one recipient." in content
    assert not Post.objects.filter(author_id=alice.identity.pk).exists()


def test_new_message_refused_when_blocked():
    alice, client = _member("alice")
    bob, _ = _member("bob")
    bob.identity.block(alice.identity)

    content = _start(client, "@bob", "hi").content.decode()

    assert "You cannot send messages to @bob." in content
    assert not Post.objects.filter(author_id=alice.identity.pk).exists()


def test_conversation_page_and_reply():
    alice, alice_client = _member("alice")
    bob, bob_client = _member("bob")
    _member("carol")
    _start(alice_client, "@bob @carol", "first")
    first = Post.objects.get(author_id=alice.identity.pk)
    url = reverse("social:conversation", args=[first.conversation_id])

    content = bob_client.get(url).content.decode()
    assert "first" in content
    assert "alice" in content
    # the header renders a context variable named "messages" as flash messages
    assert "&lt;Post" not in content
    assert _membership(bob, first.conversation_id).unread is False

    response = bob_client.post(
        reverse("social:conversation_reply", args=[first.conversation_id]),
        {"content": "second"},
    )
    assert response.status_code == 302
    reply = Post.objects.get(author_id=bob.identity.pk)
    assert reply.conversation_id == first.conversation_id
    assert reply.in_reply_to == first.object_uri
    assert reply.visibility == Post.Visibilities.mentioned
    assert {i.username for i in reply.mentions.all()} == {"alice", "carol"}
    assert _membership(alice, first.conversation_id).unread is True
    conversation = Conversation.objects.get(pk=first.conversation_id)
    assert conversation.last_post_id == reply.pk

    listing = alice_client.get(reverse("social:conversations")).content.decode()
    assert "second" in listing
    assert 'class="unread"' in listing


def test_outsider_cannot_open_or_reply():
    alice, alice_client = _member("alice")
    _member("bob")
    _, outsider = _member("mallory")
    _start(alice_client, "@bob", "private")
    conversation_id = Post.objects.get(author_id=alice.identity.pk).conversation_id

    assert (
        outsider.get(reverse("social:conversation", args=[conversation_id])).status_code
        == 404
    )
    assert (
        outsider.post(
            reverse("social:conversation_reply", args=[conversation_id]),
            {"content": "let me in"},
        ).status_code
        == 404
    )
    assert (
        "private" not in outsider.get(reverse("social:conversations")).content.decode()
    )


def test_dismiss_and_unread():
    alice, alice_client = _member("alice")
    bob, bob_client = _member("bob")
    _start(alice_client, "@bob", "hello")
    conversation_id = Post.objects.get(author_id=alice.identity.pk).conversation_id

    bob_client.post(reverse("social:conversation_dismiss", args=[conversation_id]))
    assert (
        "hello" not in bob_client.get(reverse("social:conversations")).content.decode()
    )

    alice_client.post(
        reverse("social:conversation_reply", args=[conversation_id]),
        {"content": "are you there"},
    )
    assert (
        "are you there"
        in bob_client.get(reverse("social:conversations")).content.decode()
    )

    alice_client.post(reverse("social:conversation_unread", args=[conversation_id]))
    assert _membership(alice, conversation_id).unread is True


def test_unread_status_flags_messages():
    alice, alice_client = _member("alice")
    _, bob_client = _member("bob")
    status = reverse("social:unread_notifications_status")
    assert "'has-unread', false" in bob_client.get(status).content.decode()

    _start(alice_client, "@bob", "ping")

    assert "'has-unread', true" in bob_client.get(status).content.decode()


def test_mention_notification_links_to_conversation():
    alice, alice_client = _member("alice")
    bob, bob_client = _member("bob")
    _start(alice_client, "@bob", "ping")
    post = Post.objects.get(author_id=alice.identity.pk)
    TimelineEvent.objects.create(
        identity_id=bob.identity.pk,
        type=TimelineEvent.Types.mentioned,
        subject_post=post,
        subject_identity_id=alice.identity.pk,
    )

    content = bob_client.get(reverse("social:events") + "?type=mention").content
    content = content.decode()

    assert "sent you a message" in content
    assert reverse("social:conversation", args=[post.conversation_id]) in content
    assert "reply_to_post" not in content


def test_profile_message_button_opens_messages():
    alice, client = _member("alice")
    bob, _ = _member("bob")
    content = client.get(bob.identity.url).content.decode()
    assert reverse("social:conversation_new") + "?to=@bob%40" in content

    # the button fills in the full handle of a local member
    _start(client, f"@{bob.identity.full_handle}", "hello")
    post = Post.objects.get(author_id=alice.identity.pk)
    assert list(post.mentions.values_list("pk", flat=True)) == [bob.identity.pk]


def test_message_html_drops_leading_mentions():
    domain, _ = Domain.objects.get_or_create(
        domain="remote.example", defaults={"local": False}
    )
    author = Identity.objects.create(
        actor_uri="https://remote.example/users/dan/",
        local=False,
        username="dan",
        domain=domain,
    )
    post = Post.objects.create(
        author=author,
        local=False,
        object_uri="https://remote.example/users/dan/statuses/1",
        content=(
            '<p><span class="h-card" translate="no">'
            '<a href="https://example.com/@alice" class="u-url mention">'
            "@<span>alice</span></a></span> "
            '<span class="h-card"><a href="https://example.com/@bob" '
            'class="u-url mention">@<span>bob</span></a></span> '
            "see you at 8, @<span>carol</span></p>"
        ),
        type="Note",
        visibility=Post.Visibilities.mentioned,
        state="fanned_out",
    )
    html = message_html(post)
    assert html.startswith("<p>see you at 8")
    assert "alice" not in html

    post.content = (
        '<p><span class="h-card"><a href="https://example.com/@alice" '
        'class="u-url mention">@<span>alice</span></a></span></p>'
    )
    assert "alice" in message_html(post)


def test_conversation_list_query_count_is_flat():
    alice, alice_client = _member("alice")
    friends = [_member(f"friend{n}")[0] for n in range(6)]

    def count() -> int:
        with (
            CaptureQueriesContext(connections["default"]) as d,
            CaptureQueriesContext(connections["takahe"]) as t,
        ):
            assert alice_client.get(reverse("social:conversations")).status_code == 200
        return len(d.captured_queries) + len(t.captured_queries)

    for friend in friends[:2]:
        Takahe.post(
            alice.identity.pk,
            "hi",
            Takahe.Visibilities.mentioned,
            mentions=[Takahe.get_identity(friend.identity.pk)],
        )
    count()  # session and site config load on the first request only
    few = count()
    for friend in friends[2:]:
        Takahe.post(
            alice.identity.pk,
            "hi",
            Takahe.Visibilities.mentioned,
            mentions=[Takahe.get_identity(friend.identity.pk)],
        )
    assert count() == few
