import pytest
from django.test import Client
from django.urls import reverse

from takahe.models import Post, TimelineEvent
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def test_notification_page_header_and_filters():
    _, client = _member("notified")
    content = client.get(reverse("social:notification")).content.decode()

    assert 'class="dc-mh"' in content
    assert '<a class="on" aria-current="page">All</a>' in content
    assert f'href="{reverse("social:notification")}?type=mention"' in content
    assert f'href="{reverse("social:notification")}?type=follow"' in content
    # the same two column shell and sidebar the feed page uses
    assert "dc-column" not in content
    assert 'class="grid__main"' in content
    assert "grid__aside" in content

    content = client.get(
        reverse("social:notification") + "?type=follow"
    ).content.decode()
    assert '<a class="on" aria-current="page">Follows</a>' in content


def test_follow_notification_renders_as_card():
    user, client = _member("followee")
    follower = User.register(email="follower@example.com", username="follower")
    TimelineEvent.objects.create(
        identity_id=user.identity.pk,
        type=TimelineEvent.Types.followed,
        subject_identity_id=follower.identity.pk,
    )

    content = client.get(reverse("social:events")).content.decode()

    assert 'class="activity dc-surface dc-notice unread"' in content
    assert "followed you" in content
    assert "follower" in content


def test_feed_page_carries_the_profile_sidebar():
    user, client = _member("columnist")
    content = client.get(reverse("social:feed")).content.decode()

    # the same two column shell and sidebar the member's own page uses
    assert 'class="feed-page nav-page-feed"' in content
    assert "dc-column" not in content
    assert "grid__main" in content
    assert "grid__aside" in content
    assert user.identity.display_name in content
    assert "Current targets" in content


def _post_by(author: User, text: str) -> Post:
    return Post.objects.create(
        author=author.identity.takahe_identity,
        local=True,
        object_uri=f"https://example.com/objects/{author.username}",
        content=f"<p>{text}</p>",
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )


def test_post_author_handle_shows_in_timeline_and_notifications():
    user, client = _member("handleviewer")
    fan = User.register(email="handlefan@example.com", username="handlefan")
    post = _post_by(user, "handle post")
    TimelineEvent.objects.create(
        identity_id=user.identity.pk,
        type=TimelineEvent.Types.post,
        subject_post=post,
        subject_identity_id=user.identity.pk,
    )
    TimelineEvent.objects.create(
        identity_id=user.identity.pk,
        type=TimelineEvent.Types.liked,
        subject_post=post,
        subject_identity_id=fan.identity.pk,
    )
    handle_line = f'<div class="post_handle">@{post.author.handle}</div>'

    feed = client.get(reverse("social:data")).content.decode()
    assert "handle post" in feed
    assert handle_line in feed

    notifications = client.get(reverse("social:events")).content.decode()
    assert "liked your post" in notifications
    assert handle_line in notifications

    single = client.get(f"/@{user.username}/posts/{post.pk}/").content.decode()
    assert "handle post" in single
    assert "post_handle" not in single
