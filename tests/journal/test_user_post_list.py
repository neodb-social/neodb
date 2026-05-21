from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from journal.models import Mark, ShelfType
from takahe.models import Identity as TakaheIdentity
from takahe.models import Post
from takahe.utils import Takahe
from users.models import User


def _set_locked(identity, locked: bool):
    TakaheIdentity.objects.filter(pk=identity.pk).update(
        manually_approves_followers=locked
    )
    if "takahe_identity" in identity.__dict__:
        del identity.__dict__["takahe_identity"]


def _user_post_list_url(handle):
    return reverse("journal:user_post_list", kwargs={"user_name": handle})


@pytest.fixture
def alice_with_posts(db):
    book_old = Edition.objects.create(title="Old Posts Page Book")
    book_new = Edition.objects.create(title="New Posts Page Book")
    alice = User.register(email="alice_posts@example.com", username="aliceposts")
    Mark(alice.identity, book_old).update(ShelfType.WISHLIST, "old", None, [], 0)
    shelfmember = Mark(alice.identity, book_old).shelfmember
    assert shelfmember is not None
    old_post = shelfmember.latest_post
    assert old_post is not None
    Mark(alice.identity, book_new).update(ShelfType.WISHLIST, "new", None, [], 0)
    Post.objects.filter(pk=old_post.pk).update(
        published=timezone.now() - timedelta(days=200)
    )
    return alice, old_post.pk


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_anonymous_window_limited(alice_with_posts):
    alice, old_pk = alice_with_posts
    client = Client()
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    posts = response.context["posts"]
    assert posts, "anonymous viewer should see recent posts"
    assert all(p.pk != old_pk for p in posts), "200-day-old post must be filtered out"
    assert response.context["limited_window_days"] == 90
    assert response.context["show_empty"] is False


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_anonymous_empty_when_locked(alice_with_posts):
    alice, _ = alice_with_posts
    _set_locked(alice.identity, True)
    client = Client()
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    assert response.context["show_empty"] is True
    assert response.context["posts"] == []


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_non_follower_window_limited(alice_with_posts):
    alice, old_pk = alice_with_posts
    bob = User.register(email="bob_posts@example.com", username="bobposts")
    client = Client()
    client.force_login(bob, backend="mastodon.auth.OAuth2Backend")
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    posts = response.context["posts"]
    assert posts
    assert all(p.pk != old_pk for p in posts)
    assert response.context["limited_window_days"] == 90


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_non_follower_empty_when_locked(alice_with_posts):
    alice, _ = alice_with_posts
    _set_locked(alice.identity, True)
    bob = User.register(email="bob_locked@example.com", username="boblocked")
    client = Client()
    client.force_login(bob, backend="mastodon.auth.OAuth2Backend")
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    assert response.context["show_empty"] is True


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_follower_sees_all(alice_with_posts):
    alice, old_pk = alice_with_posts
    _set_locked(alice.identity, True)
    bob = User.register(email="bob_follow@example.com", username="bobfollow")
    bob.identity.follow(alice.identity, force_accept=True)
    Takahe._force_state_cycle()
    client = Client()
    client.force_login(bob, backend="mastodon.auth.OAuth2Backend")
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    assert response.context["show_empty"] is False
    assert response.context["limited_window_days"] is None
    post_pks = {p.pk for p in response.context["posts"]}
    assert old_pk in post_pks


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_self_sees_all(alice_with_posts):
    alice, old_pk = alice_with_posts
    _set_locked(alice.identity, True)
    client = Client()
    client.force_login(alice, backend="mastodon.auth.OAuth2Backend")
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    assert response.context["show_empty"] is False
    assert response.context["limited_window_days"] is None
    post_pks = {p.pk for p in response.context["posts"]}
    assert old_pk in post_pks
