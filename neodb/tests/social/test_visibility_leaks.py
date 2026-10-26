import pytest
from django.contrib.auth.models import AnonymousUser

from social.templatetags.social_posts import quoted_post_for
from social.views import NotificationEvent
from takahe.models import Follow, Post, TimelineEvent
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _user(username: str) -> User:
    return User.register(email=f"{username}@example.com", username=username)


def _post(author: User, content: str, visibility: int, **kwargs) -> Post:
    return Post.objects.create(
        author=author.identity.takahe_identity,
        local=True,
        object_uri=f"https://example.com/objects/{author.username}/{Post.objects.count()}",
        content=f"<p>{content}</p>",
        visibility=visibility,
        state="fanned_out",
        **kwargs,
    )


def test_pending_follow_does_not_unlock_followers_only_posts() -> None:
    author, requester = _user("locked_author"), _user("requester")
    secret = _post(author, "secret", Post.Visibilities.followers)
    follow = Follow.objects.create(
        source_id=requester.identity.pk,
        target_id=author.identity.pk,
        uri="https://example.com/follow/1",
        state="pending_approval",
    )
    viewer = requester.identity.takahe_identity
    visible = Post.objects.not_hidden().visible_to(viewer).filter(pk=secret.pk)

    assert not visible.exists()

    follow.state = "accepted"
    follow.save(update_fields=["state"])
    assert visible.exists()

    follow.state = "undone"
    follow.save(update_fields=["state"])
    assert not visible.exists()


def test_mention_notification_hides_a_parent_the_viewer_cannot_see() -> None:
    author, attacker, viewer = (
        _user("private_author"),
        _user("attacker"),
        _user("viewer"),
    )
    secret = _post(author, "secret parent", Post.Visibilities.followers)
    reply = _post(
        attacker,
        "look at this",
        Post.Visibilities.public,
        in_reply_to=secret.object_uri,
    )
    event = TimelineEvent.objects.create(
        identity_id=viewer.identity.pk,
        type=TimelineEvent.Types.mentioned,
        subject_post=reply,
        subject_identity_id=attacker.identity.pk,
    )

    assert NotificationEvent(event).post is None

    Takahe.follow(viewer.identity.pk, author.identity.pk, force_accept=True)
    assert NotificationEvent(event).post == secret


def test_quote_embed_respects_the_viewers_visibility() -> None:
    author, quoter = _user("quoted_author"), _user("quoter")
    secret = _post(author, "secret quoted", Post.Visibilities.followers)
    quote = _post(
        quoter, "quoting", Post.Visibilities.public, quote_url=secret.object_uri
    )

    assert quoted_post_for(quote, AnonymousUser()) is None
    assert quoted_post_for(quote, quoter) is None
    assert quoted_post_for(quote, author) == secret
