import pytest
from django.test import Client
from django.urls import reverse

from takahe.models import TimelineEvent
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
    # the page is one column: no sidebar, no grid wrapper
    assert "grid__aside" not in content
    assert 'class="grid__main"' not in content

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


def test_feed_page_is_one_column():
    _, client = _member("columnist")
    content = client.get(reverse("social:feed")).content.decode()
    assert 'class="feed-page dc-column nav-page-feed"' in content
    assert "grid__aside" not in content
