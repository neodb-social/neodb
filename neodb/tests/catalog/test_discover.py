from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from catalog.jobs.discover import DiscoverGenerator
from catalog.models import Edition, Movie, Podcast, PodcastEpisode, TVSeason, TVShow
from common.models import SiteConfig
from journal.models import Collection, Mark, ShelfType
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def site_config(monkeypatch):
    """Patchable site options; ``__forced__`` stops views reloading them."""
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig.system, "min_marks_for_discover", 0)
    return SiteConfig.system


def _member_client(user: User) -> Client:
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def _mark_by_many(item, n: int, prefix: str) -> list[User]:
    users = []
    for i in range(n):
        u = User.register(email=f"{prefix}{i}@example.com", username=f"{prefix}{i}")
        Mark(u.identity, item).update(ShelfType.COMPLETE, rating_grade=8)
        users.append(u)
    return users


class TestDiscoverJob:
    def test_caches_weekly_marks_spotlight_and_collection_meta(self, site_config):
        book = Edition.objects.create(title="Trending Book")
        readers = _mark_by_many(book, 3, "reader")
        collection = Collection.objects.create(
            owner=readers[0].identity, title="Picks", visibility=0
        )
        collection.append_item(book)

        DiscoverGenerator().run()

        shelf = cache.get("trending_book")
        cached = next(i for i in shelf if i.pk == book.pk)
        assert cached.weekly_marks == 3
        assert cached.rating_count == 3

        spotlight = cache.get("discover_spotlight")
        spot = next(i for i in spotlight if i.pk == book.pk)
        assert spot.weekly_marks == 3

        meta = cache.get("discover_collection_meta")[collection.pk]
        assert meta["count"] == 1
        assert meta["covers"] == [book.display_cover_image_url]
        assert meta["owner"] == readers[0].identity.display_name

    def test_show_counts_marks_on_its_seasons(self, site_config):
        show = TVShow.objects.create(title="Long Show")
        season = TVSeason.objects.create(title="Season 1", show=show, season_number=1)
        _mark_by_many(season, 2, "viewer")

        DiscoverGenerator().run()

        # the season trends, but the shelf lists its show with the count
        shelf = cache.get("trending_tv")
        assert [i.pk for i in shelf] == [show.pk]
        assert shelf[0].weekly_marks == 2


class TestDiscoverPage:
    def test_anonymous_gets_hero_no_editor(self, site_config):
        site_config.discover_show_popular_posts = False
        response = Client().get("/discover/")
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="hero"' in content
        assert 'id="layoutEditButton"' not in content
        assert 'id="discover_posts"' not in content

    def test_anonymous_posts_section_follows_site_flag(self, site_config):
        site_config.discover_show_popular_posts = True
        content = Client().get("/discover/").content.decode()
        assert 'id="discover_posts"' in content
        assert "data-nosnippet" in content

    def test_member_sees_editor_posts_and_stable_section_ids(self, site_config):
        site_config.discover_show_popular_posts = False
        book = Edition.objects.create(title="Shelf Book")
        _mark_by_many(book, 1, "shelver")
        DiscoverGenerator().run()

        member = User.register(email="member@example.com", username="member")
        member.preference.discover_layout = [
            {"id": "trending_book", "visibility": True},
            {"id": "featured_collections", "visibility": False},
        ]
        member.preference.save(update_fields=["discover_layout"])

        content = _member_client(member).get("/discover/").content.decode()
        assert 'id="layoutEditButton"' in content
        # members always get a posts section, even without a curated list
        assert 'id="discover_posts"' in content
        assert 'id="hero"' not in content
        for section_id in ("trending_book", "trending_movie", "featured_collections"):
            assert f'id="{section_id}"' in content
        assert "Shelf Book" in content

    def test_legacy_hidden_recommendations_hide_personal_rows(
        self, site_config, monkeypatch
    ):
        site_config.enable_recommendations = True
        books = [Edition.objects.create(title=f"Reco {i}") for i in range(3)]
        monkeypatch.setattr(
            "catalog.views.view.for_you", lambda user, limit: list(books)
        )
        member = User.register(email="reco@example.com", username="reco")
        Mark(member.identity, Movie.objects.create(title="Seen")).update(
            ShelfType.COMPLETE
        )
        client = _member_client(member)

        content = client.get("/discover/").content.decode()
        assert 'id="for_you"' in content
        # both rows sit in one layout-editable section under the old id
        assert content.index('<div class="sortable">') < content.index(
            'id="recommendations"'
        )

        # the old single "recommendations" section, hidden in the layout editor
        member.preference.discover_layout = [
            {"id": "recommendations", "visibility": False}
        ]
        member.preference.save(update_fields=["discover_layout"])
        content = client.get("/discover/").content.decode()
        assert 'id="for_you"' not in content
        assert 'id="from_circles"' not in content
        # the section stays in the page so the editor can show it again
        assert 'id="recommendations"' in content

    def test_new_member_gets_onboarding_until_first_mark(self, site_config):
        member = User.register(email="fresh@example.com", username="fresh")
        client = _member_client(member)
        assert 'id="onboarding"' in client.get("/discover/").content.decode()

        Mark(member.identity, Movie.objects.create(title="First Film")).update(
            ShelfType.WISHLIST
        )
        assert 'id="onboarding"' not in client.get("/discover/").content.decode()

    def test_category_page_lists_shelf_and_rejects_unknown(self, site_config):
        book = Edition.objects.create(title="Grid Book")
        _mark_by_many(book, 1, "gridder")
        DiscoverGenerator().run()

        response = Client().get("/discover/book/")
        assert response.status_code == 200
        assert "Grid Book" in response.content.decode()
        assert Client().get("/discover/nonsense/").status_code == 404


class TestDiscoverPosts:
    @pytest.fixture
    def posts(self, site_config):
        movie = Movie.objects.create(title="Talked About")
        public = User.register(email="pub@example.com", username="pub")
        Mark(public.identity, movie).update(
            ShelfType.COMPLETE, comment_text="public thoughts", visibility=0
        )
        quiet = User.register(email="quiet@example.com", username="quiet")
        quiet_mark = Mark(quiet.identity, movie)
        quiet_mark.update(
            ShelfType.COMPLETE, comment_text="quiet thoughts", visibility=0
        )
        # an unlisted ("quiet public") post: members may see it, anonymous not
        for post_id in quiet_mark.all_post_ids:
            Takahe.update_post(post_id, visibility=1)
        return public

    def test_anonymous_gets_public_posts_only_and_noindex(self, site_config, posts):
        site_config.discover_show_popular_posts = True
        DiscoverGenerator().run()

        response = Client().get("/discover/popular-posts/")
        assert response.status_code == 200
        assert response["X-Robots-Tag"] == "noindex"
        content = response.content.decode()
        assert "public thoughts" in content
        assert "quiet thoughts" not in content

    def test_member_gets_unlisted_posts_too(self, site_config, posts):
        site_config.discover_show_popular_posts = True
        DiscoverGenerator().run()

        content = _member_client(posts).get("/discover/popular-posts/").content.decode()
        assert "public thoughts" in content
        assert "quiet thoughts" in content

    def test_anonymous_never_sees_members_who_require_login(self, site_config, posts):
        site_config.discover_show_popular_posts = True
        identity = posts.identity
        identity.anonymous_viewable = False
        identity.save(update_fields=["anonymous_viewable"])
        DiscoverGenerator().run()

        assert (
            "public thoughts"
            not in Client().get("/discover/popular-posts/").content.decode()
        )
        # members still see the post, the rule is about anonymous visitors
        assert (
            "public thoughts"
            in _member_client(posts).get("/discover/popular-posts/").content.decode()
        )

    def test_anonymous_never_sees_quotes_of_members_who_require_login(
        self, site_config, posts
    ):
        site_config.discover_show_popular_posts = True
        private = User.register(email="private@example.com", username="private")
        private.identity.anonymous_viewable = False
        private.identity.save(update_fields=["anonymous_viewable"])
        original = Takahe.post(
            private.identity.pk, "secret thoughts", Takahe.Visibilities.public
        )
        assert original is not None
        quote = Takahe.post(
            posts.identity.pk,
            "look at this",
            Takahe.Visibilities.public,
            quote_url=original.object_uri,
        )
        assert quote is not None
        cache.set("popular_posts", [quote.pk])

        anonymous = Client().get("/discover/popular-posts/").content.decode()
        assert "look at this" in anonymous
        assert "secret thoughts" not in anonymous

        member = _member_client(posts).get("/discover/popular-posts/")
        assert "secret thoughts" in member.content.decode()

    def test_flag_off_keeps_posts_for_members_only(self, site_config, posts):
        site_config.discover_show_popular_posts = False

        anonymous = Client().get("/discover/popular-posts/")
        assert anonymous.status_code == 200
        assert "public thoughts" not in anonymous.content.decode()

        member = _member_client(posts).get("/discover/popular-posts/")
        assert "public thoughts" in member.content.decode()


class TestOriginalShows:
    def test_rotation_keeps_the_newest_episode_of_each_show_first(
        self, site_config, monkeypatch
    ):
        site_config.discover_show_verified_podcasts = True
        episodes = []
        for p in range(2):
            program = Podcast.objects.create(title=f"Show {p}")
            for n in range(3):
                episodes.append(
                    PodcastEpisode.objects.create(
                        title=f"Show {p} episode {n}",
                        program=program,
                        pub_date=timezone.now() - timedelta(days=n),
                    )
                )
        # the job caches episodes newest first, across all shows
        episodes.sort(key=lambda e: e.pub_date, reverse=True)
        cache.set("public_gallery", [{"name": "original_episodes"}])
        cache.set("original_episodes", episodes)
        # minute 54 rotates by 9, which used to put an old episode in front
        fixed = timezone.now().replace(minute=54)
        monkeypatch.setattr(
            "catalog.views.view.timezone", SimpleNamespace(now=lambda: fixed)
        )

        response = Client().get("/discover/")

        shows = response.context["original_shows"]
        assert len(shows) == 2
        for show in shows:
            newest = max(show["episodes"], key=lambda e: e.pub_date)
            assert show["episodes"][0] == newest


class TestHiddenCategories:
    def _shelves(self, site_config):
        _mark_by_many(Edition.objects.create(title="Hidden Book"), 1, "hb")
        _mark_by_many(Movie.objects.create(title="Shown Movie"), 1, "sm")
        DiscoverGenerator().run()

    def test_member_preference_hides_shelf_and_posts(self, site_config):
        site_config.discover_show_popular_posts = True
        self._shelves(site_config)
        member = User.register(email="nobooks@example.com", username="nobooks")
        member.preference.hidden_categories = ["book"]
        member.preference.save(update_fields=["hidden_categories"])
        client = _member_client(member)

        content = client.get("/discover/").content.decode()
        assert 'id="trending_book"' not in content
        assert 'id="trending_movie"' in content
        assert "Hidden Book" not in content

        posts = client.get("/discover/popular-posts/").content.decode()
        assert "Hidden Book" not in posts
        assert "Shown Movie" in posts

    def test_site_setting_hides_shelf_for_everyone(self, site_config):
        self._shelves(site_config)
        site_config.hidden_categories = ["movie"]

        content = Client().get("/discover/").content.decode()
        assert 'id="trending_movie"' not in content
        assert 'id="trending_book"' in content
