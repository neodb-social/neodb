from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from lxml import html

from catalog.models import Edition
from journal.models import Article, Collection, Mark, Note, ShelfType
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


def _set_group(identity):
    TakaheIdentity.objects.filter(pk=identity.pk).update(actor_type="group")
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
def test_user_post_list_anonymous_group_redirects_to_login(alice_with_posts):
    alice, _ = alice_with_posts
    _set_group(alice.identity)
    client = Client()
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    # home_anonymous.html is the gate page used by profile() for the same case.
    assert response.templates[0].name == "users/home_anonymous.html"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_anonymous_group_htmx_returns_empty(alice_with_posts):
    alice, _ = alice_with_posts
    _set_group(alice.identity)
    client = Client()
    response = client.get(
        _user_post_list_url(alice.identity.handle), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert response.content == b""


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
    post_pks = {p.pk for p in response.context["posts"]}
    assert old_pk in post_pks


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_get_recent_posts_keyset_follows_published_not_pk():
    """Keyset paging stays correct when pk order != published order and when
    two posts share a published time (published-keyset fix)."""
    alice = User.register(email="alice_keyset@example.com", username="alicekeyset")
    posts = []
    for i in range(4):
        book = Edition.objects.create(title=f"Keyset Book {i}")
        Mark(alice.identity, book).update(ShelfType.WISHLIST, "x", None, [], 0)
        shelfmember = Mark(alice.identity, book).shelfmember
        assert shelfmember is not None and shelfmember.latest_post is not None
        posts.append(shelfmember.latest_post)
    p0, p1, p2, p3 = posts  # pk strictly ascending (Snowflake creation order)
    now = timezone.now()
    # Published order deliberately mismatches pk order: p3 and p0 share the
    # newest time (p3 has the larger pk, so wins the -pk tiebreak), then p1,
    # then p2. Expected (-published, -pk) order: [p3, p0, p1, p2].
    Post.objects.filter(pk=p0.pk).update(published=now)
    Post.objects.filter(pk=p3.pk).update(published=now)
    Post.objects.filter(pk=p1.pk).update(published=now - timedelta(days=1))
    Post.objects.filter(pk=p2.pk).update(published=now - timedelta(days=2))

    viewer_pk = alice.identity.pk
    page1 = list(Takahe.get_recent_posts(alice.identity.pk, viewer_pk, days=None)[:2])
    assert [p.pk for p in page1] == [p3.pk, p0.pk]
    page2 = list(
        Takahe.get_recent_posts(
            alice.identity.pk, viewer_pk, days=None, before_pk=page1[-1].pk
        )[:2]
    )
    # No skip and no duplicate of the same-published p3/p0 already shown.
    assert [p.pk for p in page2] == [p1.pk, p2.pk]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_user_post_list_self_sees_all(alice_with_posts):
    alice, old_pk = alice_with_posts
    _set_locked(alice.identity, True)
    client = Client()
    client.force_login(alice, backend="mastodon.auth.OAuth2Backend")
    response = client.get(_user_post_list_url(alice.identity.handle))
    assert response.status_code == 200
    assert response.context["show_empty"] is False
    post_pks = {p.pk for p in response.context["posts"]}
    assert old_pk in post_pks


def _menu_links(content: bytes) -> set[str]:
    document = html.fromstring(content)
    menus = document.xpath('//span[@class="post-action-menu"]')
    return {
        link
        for menu in menus
        for link in menu.xpath(".//a/@href | .//a/@hx-get | .//a/@hx-post")
    }


@pytest.fixture
def menu_users():
    author = User.register(email="menu_author@example.com", username="menuauthor")
    other = User.register(email="menu_other@example.com", username="menuother")
    author_client = Client()
    author_client.force_login(author, backend="mastodon.auth.OAuth2Backend")
    other_client = Client()
    other_client.force_login(other, backend="mastodon.auth.OAuth2Backend")
    return author, author_client, other, other_client


@pytest.mark.django_db(databases="__all__")
def test_own_cards_menu_offers_edit_and_delete(menu_users):
    author, author_client, _, other_client = menu_users
    article = Article.update_local_article(
        owner=author.identity, title="Menu", body="Body", visibility=0
    )
    plain = Takahe.post(author.identity.pk, "Plain words", Takahe.Visibilities.public)
    assert plain is not None
    book = Edition.objects.create(title="Menu Mark Book")
    Mark(author.identity, book).update(ShelfType.WISHLIST, "noted", None, [], 0)
    url = _user_post_list_url(author.identity.handle)
    response = author_client.get(url)
    assert {
        article.edit_url,
        article.delete_url,
        reverse("journal:post_edit", args=[plain.pk]),
        reverse("journal:post_delete", args=[plain.pk]),
    } <= _menu_links(response.content)
    # a mark is edited from its item, so its card gets no menu
    assert response.content.decode().count('class="post-action-menu"') == 2

    assert not _menu_links(other_client.get(url).content)


@pytest.mark.django_db(databases="__all__")
def test_article_and_collection_pages_menu_offers_delete(menu_users):
    author, author_client, _, other_client = menu_users
    article = Article.update_local_article(
        owner=author.identity, title="Menu", body="Body", visibility=0
    )
    collection = Collection.objects.create(
        owner=author.identity, title="Menu Collection", visibility=0
    )
    assert collection.latest_post is not None
    for piece in (article, collection):
        links = _menu_links(author_client.get(piece.url).content)
        assert {piece.edit_url, piece.delete_url} <= links
        links = _menu_links(other_client.get(piece.url).content)
        assert piece.edit_url not in links
        assert piece.delete_url not in links


@pytest.mark.django_db(databases="__all__")
def test_own_reply_menu_offers_edit_and_delete(menu_users):
    author, author_client, other, other_client = menu_users
    parent = Takahe.post(other.identity.pk, "Parent", Takahe.Visibilities.public)
    assert parent is not None
    reply = Takahe.post(
        author.identity.pk,
        "A reply",
        Takahe.Visibilities.public,
        reply_to_pk=parent.pk,
    )
    assert reply is not None
    url = reverse("journal:post_replies", args=[parent.pk])
    assert {
        reverse("journal:post_edit", args=[reply.pk]),
        reverse("journal:post_delete", args=[reply.pk]),
    } <= _menu_links(author_client.get(url).content)
    assert not _menu_links(other_client.get(url).content)


@pytest.mark.django_db(databases="__all__")
def test_own_note_menu_offers_edit_and_delete(menu_users):
    author, author_client, _, other_client = menu_users
    book = Edition.objects.create(title="Menu Note Book")
    Mark(author.identity, book).update(ShelfType.PROGRESS, "", None, [], 0)
    shelfmember = Mark(author.identity, book).shelfmember
    assert shelfmember is not None
    mark_post = shelfmember.latest_post
    assert mark_post is not None
    note = Note.objects.create(
        owner=author.identity, item=book, content="A note", visibility=0
    )
    assert note.latest_post is not None
    edit = reverse("journal:note", args=[book.uuid, note.uuid])
    delete = reverse("journal:post_delete", args=[note.latest_post.pk])
    # the note shows as a reply to the mark, and as a card of its own
    for url in (
        reverse("journal:post_replies", args=[mark_post.pk]),
        _user_post_list_url(author.identity.handle),
    ):
        response = author_client.get(url)
        assert {edit, delete} <= _menu_links(response.content)
        assert "Are you sure to delete this note and its post?" in (
            response.content.decode()
        )
        assert delete not in _menu_links(other_client.get(url).content)

    author_client.post(delete)
    assert not Note.objects.filter(pk=note.pk).exists()
