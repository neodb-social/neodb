import importlib

import pytest
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from lxml import html

from takahe.models import Domain, Post, PostInteraction, TimelineEvent
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity, User

pytestmark = pytest.mark.django_db(databases="__all__", transaction=True)

COMMUNITY_URL = "https://forum.example/c/readers"


def _user(name: str) -> User:
    return User.register(email=f"{name}@example.com", username=name)


def _group(name: str) -> APIdentity:
    identity = _user(name).identity
    TakaheIdentity.objects.filter(pk=identity.pk).update(actor_type="group")
    identity.__dict__.pop("takahe_identity", None)
    return identity


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def _follow_list(client: Client, owner: APIdentity, list_type: str, **params):
    url = reverse("journal:user_follow_list", args=[owner.handle, list_type])
    return client.get(url, params)


@pytest.fixture
def alice(db) -> User:
    return _user("alicegroups")


@pytest.fixture
def bob(db) -> User:
    return _user("bobgroups")


def test_groups_tab_lists_only_followed_groups(alice, bob):
    club = _group("clubone")
    _group("clubnotfollowed")
    alice.identity.follow(club)
    alice.identity.follow(bob.identity)
    Takahe._force_state_cycle()
    client = _login(alice)

    response = _follow_list(client, alice.identity, "groups")

    assert response.status_code == 200
    assert response.context["identities"] == [club]
    page = html.fromstring(response.content.decode())
    current = page.cssselect('.follow-list-nav a[aria-current="page"]')
    assert [a.text_content().strip() for a in current] == ["Groups"]

    following = _follow_list(client, alice.identity, "following")
    assert set(following.context["identities"]) == {club, bob.identity}
    cards = html.fromstring(following.content.decode()).cssselect(".follow-card")
    labelled = [c for c in cards if c.cssselect(".follow-card__kind")]
    assert len(cards) == 2
    assert len(labelled) == 1
    assert "clubone" in labelled[0].text_content()


def test_groups_tab_pages_by_cursor(alice, monkeypatch):
    first, second = sorted([_group("clubaa"), _group("clubbb")], key=lambda g: g.pk)
    alice.identity.follow(first)
    alice.identity.follow(second)
    Takahe._force_state_cycle()
    # journal.views.profile names the view function, so take the module
    views = importlib.import_module("journal.views.profile")
    monkeypatch.setattr(views, "_FOLLOW_LIST_PAGE_SIZE", 1)
    client = _login(alice)

    page_one = _follow_list(client, alice.identity, "groups")
    assert page_one.context["identities"] == [first]
    page_two = _follow_list(
        client, alice.identity, "groups", last=page_one.context["next_cursor"]
    )
    assert page_two.context["identities"] == [second]


def test_groups_tab_keeps_the_access_rule(alice, bob):
    response = _follow_list(_login(bob), alice.identity, "groups")
    assert response.status_code == 403


def _feed(client: Client):
    return client.get(reverse("social:data"), {"typ": 0})


def _boost_event(viewer: User, booster: APIdentity, post: Post) -> None:
    interaction = PostInteraction.objects.create(
        identity_id=booster.pk, post=post, type="boost", state="fanned_out"
    )
    TimelineEvent.objects.create(
        identity_id=viewer.identity.pk,
        type="boost",
        subject_post=post,
        subject_identity_id=booster.pk,
        subject_post_interaction=interaction,
    )


def test_feed_shows_a_group_boost_as_posted_in_the_group(alice, bob):
    club = _group("clubfeed")
    carol = _user("carolgroups")
    in_group = Takahe.post(bob.identity.pk, "a thread", Takahe.Visibilities.public)
    by_person = Takahe.post(bob.identity.pk, "a post", Takahe.Visibilities.public)
    assert in_group is not None and by_person is not None
    _boost_event(alice, club, in_group)
    _boost_event(alice, carol.identity, by_person)

    response = _feed(_login(alice))

    assert response.status_code == 200
    lines = html.fromstring(response.content.decode()).cssselect(".boosted")
    text = {" ".join(line.text_content().split()) for line in lines}
    assert text == {"Posted in clubfeed", "carolgroups boosted"}
    group_line = next(line for line in lines if "Posted in" in line.text_content())
    assert group_line.cssselect("a")[0].get("href") == club.url
    assert group_line.cssselect("i.fa-users")


def _page_post(name: str, audience: str) -> Post:
    domain, _ = Domain.objects.get_or_create(
        domain="author.example", defaults={"local": False}
    )
    author = TakaheIdentity.objects.create(
        actor_uri=f"https://author.example/u/{name}",
        local=False,
        username=name,
        domain=domain,
    )
    return Post.objects.create(
        author=author,
        local=False,
        object_uri=f"https://author.example/post/{name}",
        url=f"https://author.example/post/{name}",
        content="<p>body</p>",
        type="Page",
        type_data={"object": {"name": "A thread", "audience": audience}},
        visibility=0,
        state="fanned_out",
    )


def _known_group() -> TakaheIdentity:
    domain, _ = Domain.objects.get_or_create(
        domain="forum.example", defaults={"local": False}
    )
    return TakaheIdentity.objects.create(
        actor_uri=COMMUNITY_URL,
        local=False,
        username="readers",
        domain=domain,
        actor_type="group",
    )


def test_community_of_a_page_resolves_to_the_group_known_here():
    group = _known_group()
    known = _page_post("known", COMMUNITY_URL)
    unknown = _page_post("unknown", "https://elsewhere.example/c/other")
    plain = Post.objects.create(
        author=known.author, local=False, content="<p>note</p>", visibility=0
    )

    assert known.local_community == group
    assert unknown.local_community is None
    assert plain.local_community is None


def test_communities_of_a_list_are_looked_up_once():
    group = _known_group()
    posts = [
        _page_post("one", COMMUNITY_URL),
        _page_post("two", COMMUNITY_URL),
        _page_post("three", "https://elsewhere.example/c/other"),
    ]

    with CaptureQueriesContext(connections["takahe"]) as queries:
        Takahe.prefetch_local_communities(posts)
        found = [post.local_community for post in posts]

    assert found == [group, group, None]
    assert len(queries) == 1


def test_a_list_of_group_posts_looks_its_community_up_once(alice):
    _known_group()
    club = _group("clubpages")
    for name in ("first", "second", "third"):
        PostInteraction.objects.create(
            identity_id=club.pk,
            post=_page_post(name, COMMUNITY_URL),
            type="boost",
            state="fanned_out",
        )
    url = reverse("journal:profile_posts_data", kwargs={"user_name": club.handle})
    client = _login(alice)

    with CaptureQueriesContext(connections["takahe"]) as queries:
        response = client.get(url)

    assert response.content.decode().count("c/readers@forum.example") == 3
    by_actor_uri = [
        q["sql"] for q in queries if '"users_identity"."actor_uri"' in q["sql"]
    ]
    lookups = [sql for sql in by_actor_uri if COMMUNITY_URL in sql]
    assert len(lookups) == 1


def test_feed_card_links_a_known_community_to_its_page_here(alice):
    group = _known_group()
    for name, audience in (
        ("local", COMMUNITY_URL),
        ("remote", "https://elsewhere.example/c/other"),
    ):
        TimelineEvent.objects.create(
            identity_id=alice.identity.pk,
            type="post",
            subject_post=_page_post(name, audience),
        )

    page = html.fromstring(_feed(_login(alice)).content.decode())

    links = {
        a.text_content().strip(): a
        for a in page.cssselect(".converted-object-details a")
    }
    here = links["c/readers@forum.example"]
    assert here.get("href") == group.url
    assert here.get("target") is None
    there = links["c/other@elsewhere.example"]
    assert there.get("href") == "https://elsewhere.example/c/other"
    assert there.get("target") == "_blank"
