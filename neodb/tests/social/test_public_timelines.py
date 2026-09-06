"""The opt-in world and local timelines on the home feed."""

import pytest
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from common.models import SiteConfig
from takahe.models import Domain, Identity, Post
from takahe.utils import Takahe
from users.models import User


def _enable(monkeypatch: pytest.MonkeyPatch, *, world: bool, local: bool) -> None:
    opts = SiteConfig.system.model_copy(
        update={"feed_show_world": world, "feed_show_local": local}
    )
    monkeypatch.setattr(SiteConfig, "system", opts)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


def _remote_post(content: str, username: str = "carol") -> Post:
    domain, _ = Domain.objects.get_or_create(
        domain="remote.example", defaults={"local": False}
    )
    identity = Identity.objects.create(
        actor_uri=f"https://remote.example/users/{username}/",
        local=False,
        username=username,
        domain=domain,
    )
    Takahe.get_or_create_remote_apidentity(identity)
    return Post.objects.create(
        author=identity,
        local=False,
        object_uri=f"https://remote.example/users/{username}/statuses/1",
        content=f"<p>{content}</p>",
        type="Note",
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )


@pytest.fixture
def viewer(db) -> User:
    return User.register(email="viewer@example.com", username="viewer")


@pytest.fixture
def authed(viewer: User) -> Client:
    client = Client()
    client.force_login(viewer, backend="mastodon.auth.OAuth2Backend")
    return client


@pytest.mark.django_db(databases="__all__")
class TestGating:
    def test_disabled_by_default(self, authed: Client) -> None:
        assert not SiteConfig.system.feed_show_world
        assert not SiteConfig.system.feed_show_local
        assert authed.get(reverse("social:world")).status_code == 404
        assert authed.get(reverse("social:local")).status_code == 404

    def test_data_endpoint_is_gated_too(self, authed: Client) -> None:
        # the page could be bypassed by calling the HTMX endpoint directly
        url = reverse("social:data")
        assert authed.get(f"{url}?typ=2").status_code == 404
        assert authed.get(f"{url}?typ=3").status_code == 404

    def test_each_flag_only_opens_its_own_tab(
        self, authed: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=False)
        assert authed.get(reverse("social:world")).status_code == 200
        assert authed.get(reverse("social:local")).status_code == 404

    def test_following_tabs_are_never_gated(self, authed: Client) -> None:
        assert authed.get(reverse("social:feed")).status_code == 200
        assert authed.get(reverse("social:focus")).status_code == 200


@pytest.mark.django_db(databases="__all__")
class TestTabBar:
    def test_hidden_when_disabled(self, authed: Client) -> None:
        content = authed.get(reverse("social:feed")).content.decode()
        assert reverse("social:world") not in content
        assert reverse("social:local") not in content
        assert "those you follow" in content

    def test_shown_when_enabled(
        self, authed: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        content = authed.get(reverse("social:feed")).content.decode()
        assert reverse("social:world") in content
        assert reverse("social:local") in content

    def test_active_tab_is_not_a_link(
        self, authed: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        content = authed.get(reverse("social:world")).content.decode()
        assert f'href="{reverse("social:world")}"' not in content
        assert f'href="{reverse("social:local")}"' in content


@pytest.mark.django_db(databases="__all__")
class TestContent:
    def test_local_excludes_remote_posts(
        self, authed: Client, viewer: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        Takahe.post(viewer.identity.pk, "a local post", Takahe.Visibilities.public)
        _remote_post("a remote post")
        url = reverse("social:data")

        local = authed.get(f"{url}?typ=2").content.decode()
        assert "a local post" in local
        assert "a remote post" not in local

        world = authed.get(f"{url}?typ=3").content.decode()
        assert "a local post" in world
        assert "a remote post" in world

    def test_non_public_posts_are_excluded(
        self, authed: Client, viewer: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        Takahe.post(viewer.identity.pk, "a private post", Takahe.Visibilities.followers)
        content = authed.get(f"{reverse('social:data')}?typ=3").content.decode()
        assert "a private post" not in content

    def test_cursor_pages_by_post_id(
        self, authed: Client, viewer: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        ids = []
        for i in range(3):
            post = Takahe.post(
                viewer.identity.pk, f"post {i}", Takahe.Visibilities.public
            )
            assert post is not None
            ids.append(post.pk)
        url = reverse("social:data")

        first = authed.get(f"{url}?typ=3").content.decode()
        assert "post 2" in first
        # the sentinel pages on the oldest rendered row, so ?last= is that id
        assert f"last={ids[0]}" in first

        rest = authed.get(f"{url}?typ=3&last={ids[2]}").content.decode()
        assert "post 2" not in rest
        assert "post 1" in rest


@pytest.mark.django_db(databases="__all__")
class TestQueryCount:
    """The world timeline reads posts directly rather than TimelineEvent rows,
    so it needs its own guard; tests/social/test_timeline_n_plus_one.py covers
    only the follow feed.

    Posts, identities and domains live in the ``takahe`` database, so counting
    only ``connections["default"]`` (as the follow-feed test does, because it
    asserts on journal tables) would miss every query this path is about.
    """

    def _post_as_new_author(self, start: int, count: int) -> None:
        """One plain note each from `count` distinct authors.

        Distinct authors matter twice over: a run of same-author marks
        collapses into one FeedEventGroup card, and a shared author would be
        fetched once however the queryset is built.
        """
        for i in range(start, start + count):
            author = User.register(
                email=f"author{i}@example.com", username=f"author{i}"
            )
            Takahe.post(
                author.identity.pk, f"note from author {i}", Takahe.Visibilities.public
            )

    def test_query_count_does_not_grow_with_posts(
        self, authed: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, world=True, local=True)
        url = f"{reverse('social:data')}?typ=3"

        self._post_as_new_author(0, 1)
        authed.get(url)  # warm the caches the first request populates
        with (
            CaptureQueriesContext(connections["default"]) as one_default,
            CaptureQueriesContext(connections["takahe"]) as one_takahe,
        ):
            assert authed.get(url).status_code == 200
        one = len(one_default.captured_queries) + len(one_takahe.captured_queries)

        # PAGE_SIZE is 10, so this fills a whole page
        self._post_as_new_author(1, 9)
        with (
            CaptureQueriesContext(connections["default"]) as ten_default,
            CaptureQueriesContext(connections["takahe"]) as ten_takahe,
        ):
            response = authed.get(url)
        ten = len(ten_default.captured_queries) + len(ten_takahe.captured_queries)

        assert response.status_code == 200
        assert response.content.decode().count("note from author ") == 10
        assert ten <= one, (
            "query count grew with the number of posts: "
            f"{one} for 1 post by 1 author, {ten} for 10 posts by 10 authors"
        )
