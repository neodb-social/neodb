import pytest
from django.test import Client
from django.urls import reverse

from catalog.jobs.recommendation import BuildItemSimilarity
from catalog.models import Edition, RecommendationDismissal, UserRecommendation, Work
from catalog.recommendation import (
    compute_for_user,
    dismiss_item,
    for_you,
    from_your_circles,
    restore_item,
)
from common.models import SiteConfig
from journal.models import Mark, ShelfType
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def site_config(monkeypatch):
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig, "system", SiteConfig.system.model_copy(deep=True))
    sys = SiteConfig.system
    sys.enable_recommendations = True
    sys.reco_min_source_marks = 2
    sys.reco_min_target_marks = 2
    sys.reco_similarity_top_k = 10
    sys.reco_user_top_n = 10
    sys.reco_per_user_seed_cap = 50
    sys.reco_user_mark_cap = 100
    sys.reco_user_active_days = 30
    sys.reco_user_idf_dampen = False
    sys.reco_lazy_ttl_days = 7
    sys.reco_circles_window_days = 14
    return sys


def _public_mark(identity, item, shelf=ShelfType.COMPLETE):
    Mark(identity, item).update(shelf, "", 8, [], 0)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def _cached_rows(user: User, items: list) -> None:
    """Store a precomputed list, best first, as the nightly job would."""
    UserRecommendation.objects.bulk_create(
        UserRecommendation(user=user, item=item, score=len(items) - n, category="book")
        for n, item in enumerate(items)
    )


class TestPrecompute:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        strangers = [
            User.register(email=f"pc{i}@t.com", username=f"pc{i}").identity
            for i in range(2)
        ]
        self.src = Edition.objects.create(title="Src")
        self.work = Work.objects.create(title="The Work")
        self.e1 = Edition.objects.create(title="Edition One")
        self.e2 = Edition.objects.create(title="Edition Two")
        self.work.editions.add(self.e1, self.e2)
        self.other = Edition.objects.create(title="Other")
        self.spare = Edition.objects.create(title="Spare")
        for ident in strangers:
            for item in (self.src, self.e1, self.e2, self.other, self.spare):
                _public_mark(ident, item)
        BuildItemSimilarity().run()
        self.user = User.register(email="pct@t.com", username="pct")
        _public_mark(self.user.identity, self.src)

    def _ids(self) -> set[int]:
        return {
            r.item_id for r in compute_for_user(self.user.pk, self.user.identity.pk)
        }

    def test_dismissed_item_is_not_computed(self):
        assert self.other.pk in self._ids()
        dismiss_item(self.user, self.other)
        ids = self._ids()
        assert self.other.pk not in ids
        assert self.spare.pk in ids

    def test_sibling_edition_of_dismissed_item_is_not_computed(self):
        dismiss_item(self.user, self.e1)
        ids = self._ids()
        assert self.e1.pk not in ids
        assert self.e2.pk not in ids
        assert self.other.pk in ids


class TestForYou:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.user = User.register(email="fy@t.com", username="fy")
        self.books = [Edition.objects.create(title=f"Book {i}") for i in range(6)]
        _cached_rows(self.user, self.books)

    def _ids(self, limit: int = 3) -> list[int]:
        return [i.pk for i in for_you(self.user, limit=limit)]

    def test_stored_rows_fill_the_place_of_dismissed_items(self):
        assert self._ids() == [b.pk for b in self.books[:3]]
        dismiss_item(self.user, self.books[0])
        dismiss_item(self.user, self.books[2])
        assert self._ids() == [
            b.pk for b in (self.books[1], self.books[3], self.books[4])
        ]

    def test_stored_rows_fill_the_place_of_shelved_items(self):
        _public_mark(self.user.identity, self.books[1])
        assert self._ids() == [
            b.pk for b in (self.books[0], self.books[2], self.books[3])
        ]

    def test_item_merged_after_dismissal_stays_hidden(self):
        old = Edition.objects.create(title="Old")
        dismiss_item(self.user, old)
        old.merge_to(self.books[0])
        assert self.books[0].pk not in self._ids()

    def test_dismissing_a_merged_item_stores_the_final_item(self):
        old = Edition.objects.create(title="Old")
        old.merge_to(self.books[0])
        dismiss_item(self.user, old)
        assert RecommendationDismissal.objects.get(user=self.user).item_id == (
            self.books[0].pk
        )
        assert self.books[0].pk not in self._ids()

    def test_restore_clears_dismissals_merged_into_the_item(self):
        old = Edition.objects.create(title="Old")
        dismiss_item(self.user, old)
        old.merge_to(self.books[0])
        restore_item(self.user, self.books[0])
        assert not RecommendationDismissal.objects.filter(user=self.user).exists()
        assert self.books[0].pk in self._ids()

    def test_dismissal_is_per_user(self):
        other = User.register(email="fy2@t.com", username="fy2")
        _cached_rows(other, self.books)
        dismiss_item(other, self.books[0])
        assert self.books[0].pk in self._ids()


class TestFromYourCircles:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.viewer = User.register(email="dc0@t.com", username="dc_viewer")
        friends = [
            User.register(email=f"dc{i + 1}@t.com", username=f"dc_friend{i}")
            for i in range(2)
        ]
        self.book_a = Edition.objects.create(title="Circles A")
        self.book_b = Edition.objects.create(title="Circles B")
        for f in friends:
            self.viewer.identity.follow(f.identity, True)
            _public_mark(f.identity, self.book_a)
            _public_mark(f.identity, self.book_b)

    def test_dismissed_item_is_dropped(self):
        dismiss_item(self.viewer, self.book_a)
        assert [i.pk for i in from_your_circles(self.viewer)] == [self.book_b.pk]

    def test_dismissal_after_cache_is_dropped(self):
        assert len(from_your_circles(self.viewer)) == 2
        dismiss_item(self.viewer, self.book_a)
        assert [i.pk for i in from_your_circles(self.viewer)] == [self.book_b.pk]


class TestWebViews:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.user = User.register(email="wv@t.com", username="wv")
        self.book = Edition.objects.create(title="Hide Me")
        self.client = _client(self.user)

    def test_dismiss_needs_login(self):
        url = reverse("catalog:dismiss_recommendation", args=[self.book.uuid])
        response = Client().post(url)
        assert response.status_code == 302
        assert not RecommendationDismissal.objects.exists()

    def test_dismiss_needs_post(self):
        url = reverse("catalog:dismiss_recommendation", args=[self.book.uuid])
        assert self.client.get(url).status_code == 405
        assert not RecommendationDismissal.objects.exists()

    def test_dismiss_is_idempotent_and_offers_undo(self):
        url = reverse("catalog:dismiss_recommendation", args=[self.book.uuid])
        for _ in range(2):
            response = self.client.post(url)
            assert response.status_code == 200
        assert (
            RecommendationDismissal.objects.filter(
                user=self.user, item=self.book
            ).count()
            == 1
        )
        content = response.content.decode()
        assert reverse("catalog:restore_recommendation", args=[self.book.uuid]) in (
            content
        )

    def test_dismiss_unknown_item_is_404(self):
        url = reverse("catalog:dismiss_recommendation", args=["0" * 22])
        assert self.client.post(url).status_code == 404

    def test_undo_returns_the_card(self):
        dismiss_item(self.user, self.book)
        url = reverse("catalog:restore_recommendation", args=[self.book.uuid])
        response = self.client.post(url)
        assert response.status_code == 200
        assert not RecommendationDismissal.objects.exists()
        content = response.content.decode()
        assert "Hide Me" in content
        assert 'class="dc-card-dismiss"' in content

    def test_restore_from_list_removes_the_card(self):
        dismiss_item(self.user, self.book)
        url = reverse("catalog:restore_recommendation", args=[self.book.uuid])
        response = self.client.post(url, {"remove": "1"})
        assert response.status_code == 200
        assert response.content == b""
        assert not RecommendationDismissal.objects.exists()

    def test_hidden_list_shows_only_own_dismissals(self):
        other = User.register(email="wv2@t.com", username="wv2")
        other_book = Edition.objects.create(title="Not Mine")
        dismiss_item(self.user, self.book)
        dismiss_item(other, other_book)
        response = self.client.get(reverse("catalog:hidden_recommendations"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Hide Me" in content
        assert "Not Mine" not in content
        assert (
            reverse("catalog:restore_recommendation", args=[self.book.uuid]) in content
        )

    def test_hidden_list_needs_login(self):
        response = Client().get(reverse("catalog:hidden_recommendations"))
        assert response.status_code == 302

    def test_discover_reco_cards_offer_dismiss(self, site_config, monkeypatch):
        site_config.min_marks_for_discover = 0
        _public_mark(self.user.identity, Edition.objects.create(title="Seen"))
        books = [Edition.objects.create(title=f"Reco {i}") for i in range(3)]
        monkeypatch.setattr(
            "catalog.views.view.for_you", lambda user, limit: list(books)
        )
        content = self.client.get("/discover/").content.decode()
        assert 'id="for_you"' in content
        for b in books:
            assert reverse("catalog:dismiss_recommendation", args=[b.uuid]) in content


class TestApi:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.user = User.register(email="api@t.com", username="api_user")
        self.book = Edition.objects.create(title="Api Book")
        app = Takahe.get_or_create_app(
            "Reco Dismiss Tests",
            "https://example.org",
            "https://example.org/callback",
            owner_pk=self.user.identity.pk,
        )
        token = Takahe.refresh_token(app, self.user.identity.pk, self.user.pk)
        self.auth = {"Authorization": f"Bearer {token}"}
        self.url = f"/api/me/recommendations/{self.book.uuid}/dismiss"

    def test_dismiss_and_restore(self):
        client = Client()
        response = client.post(self.url, headers=self.auth)
        assert response.status_code == 200
        assert RecommendationDismissal.objects.filter(
            user=self.user, item=self.book
        ).exists()
        response = client.delete(self.url, headers=self.auth)
        assert response.status_code == 200
        assert not RecommendationDismissal.objects.exists()

    def test_unknown_item_is_404(self):
        response = Client().post(
            f"/api/me/recommendations/{'0' * 22}/dismiss", headers=self.auth
        )
        assert response.status_code == 404

    def test_needs_token(self):
        assert Client().post(self.url).status_code == 401
        assert not RecommendationDismissal.objects.exists()

    def test_dismissed_item_leaves_blended_list(self):
        books = [Edition.objects.create(title=f"Api {i}") for i in range(3)]
        _cached_rows(self.user, books)
        client = Client()
        client.post(
            f"/api/me/recommendations/{books[0].uuid}/dismiss", headers=self.auth
        )
        data = client.get("/api/me/recommendations", headers=self.auth).json()["data"]
        assert [d["uuid"] for d in data] == [books[1].uuid, books[2].uuid]
