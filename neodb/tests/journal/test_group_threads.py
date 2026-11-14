from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from lxml import html

from takahe.models import Identity as TakaheIdentity
from takahe.models import Post, PostInteraction
from takahe.utils import Takahe
from users.models import APIdentity, User

pytestmark = pytest.mark.django_db(databases="__all__", transaction=True)


@pytest.fixture
def group(db) -> APIdentity:
    identity = User.register(email="club@example.com", username="bookclub").identity
    TakaheIdentity.objects.filter(pk=identity.pk).update(actor_type="group")
    identity.__dict__.pop("takahe_identity", None)
    return identity


@pytest.fixture
def alice(db) -> User:
    return User.register(email="alice_forum@example.com", username="aliceforum")


@pytest.fixture
def bob(db) -> User:
    return User.register(email="bob_forum@example.com", username="bobforum")


def _post(author: User, content: str, days_ago: int = 0, **kwargs) -> Post:
    post = Takahe.post(
        author.identity.pk,
        content,
        Takahe.Visibilities.public,
        post_time=timezone.now() - timedelta(days=days_ago),
        **kwargs,
    )
    assert post is not None
    return post


def _boost(group: APIdentity, post: Post) -> None:
    PostInteraction.objects.create(
        identity_id=group.pk, post=post, type="boost", state="fanned_out"
    )


def _thread(group: APIdentity, author: User, content: str, days_ago: int = 0) -> Post:
    post = _post(author, content, days_ago)
    _boost(group, post)
    return post


def _reply(author: User, parent: Post, content: str, **kwargs) -> Post:
    return _post(author, content, reply_to_pk=parent.pk, **kwargs)


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def test_threads_are_sorted_by_their_own_date(group, alice, bob):
    oldest = _thread(group, alice, "oldest", days_ago=3)
    newest = _thread(group, alice, "newest", days_ago=1)
    middle = _thread(group, bob, "middle", days_ago=2)
    _post(alice, "not posted to the group")
    _boost(group, _reply(bob, newest, "a boosted reply is not a thread"))

    threads = list(Takahe.get_group_threads(group.pk))

    assert [p.pk for p in threads] == [newest.pk, middle.pk, oldest.pk]


def test_threads_page_by_date_cursor(group, alice):
    oldest = _thread(group, alice, "oldest", days_ago=3)
    newest = _thread(group, alice, "newest", days_ago=1)
    middle = _thread(group, alice, "middle", days_ago=2)

    page = list(Takahe.get_group_threads(group.pk, before_pk=newest.pk))

    assert [p.pk for p in page] == [middle.pk, oldest.pk]
    # a cursor post that is gone pages by id instead of starting again
    gone = list(Takahe.get_group_threads(group.pk, before_pk=newest.pk + 1))
    assert [p.pk for p in gone] == [newest.pk, oldest.pk]


def test_threads_leave_out_blocked_authors_and_deleted_posts(group, alice, bob):
    kept = _thread(group, alice, "kept")
    deleted = _thread(group, alice, "deleted")
    Post.objects.filter(pk=deleted.pk).update(state="deleted")
    _thread(group, bob, "from a blocked author")
    Takahe.block(alice.identity.pk, bob.identity.pk)

    threads = list(Takahe.get_group_threads(group.pk, alice.identity.pk))

    assert [p.pk for p in threads] == [kept.pk]


def test_reply_counts_cover_every_level(group, alice, bob):
    thread = _thread(group, alice, "thread")
    quiet = _thread(group, alice, "no replies")
    first = _reply(bob, thread, "first")
    second = _reply(alice, first, "second")
    _reply(bob, second, "third")
    _reply(bob, thread, "sibling")
    gone = _reply(bob, thread, "deleted")
    Post.objects.filter(pk=gone.pk).update(state="deleted")
    private = _reply(bob, thread, "followers only")
    Post.objects.filter(pk=private.pk).update(visibility=2)

    counts = Takahe.get_thread_reply_counts([thread.pk, quiet.pk])

    assert counts == {thread.pk: 4, quiet.pk: 0}
    assert Takahe.get_thread_reply_counts([]) == {}


def test_reply_counts_end_on_a_reply_loop(group, alice, bob):
    thread = _thread(group, alice, "thread")
    reply = _reply(bob, thread, "reply")
    Post.objects.filter(pk=thread.pk).update(in_reply_to=reply.object_uri)

    assert Takahe.get_thread_reply_counts([thread.pk]) == {thread.pk: 1}


def test_reply_tree_nests_each_level(group, alice, bob):
    thread = _thread(group, alice, "thread")
    first = _reply(bob, thread, "first", days_ago=3)
    nested = _reply(alice, first, "nested", days_ago=2)
    deep = _reply(bob, nested, "deep", days_ago=1)
    sibling = _reply(alice, thread, "sibling", days_ago=1)
    Takahe.like_post(nested.pk, bob.identity.pk)

    tree = Takahe.get_reply_tree(thread.pk, bob.identity.pk)

    assert [p.pk for p in tree] == [first.pk, sibling.pk]
    assert [p.pk for p in tree[0].thread_replies or []] == [nested.pk]
    branch = (tree[0].thread_replies or [])[0]
    assert [p.pk for p in branch.thread_replies or []] == [deep.pk]
    assert tree[1].thread_replies is None
    assert branch.liked_by_current_user is True
    assert tree[0].liked_by_current_user is False


def test_reply_tree_stops_at_its_limits(group, alice, bob, monkeypatch):
    thread = _thread(group, alice, "thread")
    first = _reply(bob, thread, "first", days_ago=3)
    nested = _reply(alice, first, "nested", days_ago=2)
    _reply(bob, nested, "deep", days_ago=1)

    monkeypatch.setattr(Takahe, "THREAD_RENDER_DEPTH", 2)
    tree = Takahe.get_reply_tree(thread.pk, None)
    assert [p.pk for p in tree[0].thread_replies or []] == [nested.pk]
    assert (tree[0].thread_replies or [])[0].thread_replies is None

    monkeypatch.setattr(Takahe, "THREAD_RENDER_DEPTH", 6)
    monkeypatch.setattr(Takahe, "THREAD_MAX_REPLIES", 1)
    tree = Takahe.get_reply_tree(thread.pk, None)
    assert [p.pk for p in tree] == [first.pk]
    assert tree[0].thread_replies is None


def test_thread_title_prefers_the_object_name(alice):
    post = _post(alice, "body text")
    post.type_data = {"object": {"name": "  A named thread "}}
    assert post.thread_title == "A named thread"


def test_thread_title_falls_back_to_the_first_line(alice):
    post = _post(
        alice,
        "<p>@bookclub @someone@example.org  What to read next?</p><p>More</p>",
        content_is_html=True,
    )
    assert post.thread_title == "What to read next?"
    assert _post(alice, "plain", summary="A warning").thread_title == "A warning"


def test_group_profile_lists_threads(group, alice, bob):
    thread = _thread(group, alice, "Which translation is best?")
    _reply(bob, thread, "first")
    _reply(alice, _reply(bob, thread, "second"), "third")

    response = _login(bob).get(
        reverse("journal:profile_posts_data", kwargs={"user_name": group.handle})
    )

    assert response.status_code == 200
    assert response.templates[0].name == "group_threads.html"
    rows = html.fromstring(response.content.decode()).cssselect(".thread-row")
    assert len(rows) == 1
    link = rows[0].cssselect(".thread-row-title a")[0]
    assert link.text_content() == "Which translation is best?"
    assert link.get("href") == reverse(
        "journal:post_view", args=[thread.author.handle, thread.pk]
    )
    assert rows[0].cssselect(".thread-row-meta")[0].text_content().split()[-1] == "3"


def test_group_profile_pages_from_the_last_thread(group, alice, bob):
    older = _thread(group, alice, "older", days_ago=2)
    newer = _thread(group, alice, "newer", days_ago=1)
    url = reverse("journal:profile_posts_data", kwargs={"user_name": group.handle})
    client = _login(bob)

    first = client.get(url)
    assert f"?last={older.pk}" in first.content.decode()
    second = client.get(url, {"last": newer.pk})

    assert [p.pk for p in second.context["posts"]] == [older.pk]


def test_person_profile_keeps_the_post_feed(alice, bob):
    _post(alice, "a plain post")

    response = _login(bob).get(
        reverse(
            "journal:profile_posts_data", kwargs={"user_name": alice.identity.handle}
        )
    )

    assert response.templates[0].name == "profile_posts.html"


def _post_view_url(post: Post, author: User) -> str:
    return reverse("journal:post_view", args=[author.identity.handle, post.pk])


def test_thread_page_links_back_to_the_group(group, alice, bob):
    thread = _thread(group, alice, "thread")
    _reply(bob, thread, "reply")

    response = _login(bob).get(_post_view_url(thread, alice))

    assert response.status_code == 200
    assert response.context["thread_group"] == group
    assert response.context["thread_reply_count"] == 1
    page = html.fromstring(response.content.decode())
    assert page.cssselect(f'header.dc-mh a[href="{group.url}"]')
    loader = page.cssselect(f"#replies_{thread.pk}")[0]
    assert "thread=1" in loader.get("hx-get", "")


def test_post_page_outside_a_group_is_unchanged(group, alice, bob):
    plain = _post(alice, "plain")
    thread = _thread(group, alice, "thread")
    reply = _reply(bob, thread, "reply")
    client = _login(bob)

    for post, author in ((plain, alice), (reply, bob)):
        response = client.get(_post_view_url(post, author))
        assert "thread_group" not in response.context
        assert "thread=1" not in response.content.decode()

    anonymous = Client().get(_post_view_url(thread, alice))
    assert "thread_group" not in anonymous.context


def test_nested_replies_render_as_one_tree(group, alice, bob):
    thread = _thread(group, alice, "thread")
    first = _reply(bob, thread, "first reply", days_ago=2)
    nested = _reply(alice, first, "nested reply", days_ago=1)
    url = reverse("journal:post_replies", args=[thread.pk])
    client = _login(bob)

    tree = html.fromstring(client.get(url, {"thread": "1"}).content.decode())

    branch = tree.cssselect(f"section#replies_{thread.pk} section#replies_{first.pk}")
    assert len(branch) == 1
    assert "nested reply" in branch[0].text_content()
    assert tree.cssselect(f"#replies_{nested.pk}")
    # one form, for the post the tree belongs to
    assert len(tree.cssselect("form.reply")) == 1
    assert tree.cssselect('form.reply input[name="thread"]')
    reply_link = tree.cssselect(f'a[hx-target="#replies_{first.pk}"]')[0]
    assert "thread=1" in reply_link.get("hx-get", "")

    flat = html.fromstring(client.get(url).content.decode())
    assert not flat.cssselect(f"section#replies_{first.pk}")
    assert not flat.cssselect('form.reply input[name="thread"]')


def test_reply_in_a_thread_returns_the_tree(group, alice, bob):
    thread = _thread(group, alice, "thread")
    first = _reply(bob, thread, "first reply", days_ago=2)
    _reply(alice, first, "nested reply", days_ago=1)

    response = _login(bob).post(
        reverse("journal:post_reply", args=[thread.pk]),
        {"content": "another reply", "visibility": "0", "thread": "1"},
    )

    assert response.status_code == 200
    tree = html.fromstring(response.content.decode())
    assert (
        "nested reply"
        in tree.cssselect(f"section#replies_{first.pk}")[0].text_content()
    )
    assert "another reply" in tree.text_content()
