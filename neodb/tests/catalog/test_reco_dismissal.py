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
    similar_items,
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


class TestRefill:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        strangers = [
            User.register(email=f"rf{i}@t.com", username=f"rf{i}").identity
            for i in range(2)
        ]
        self.src = Edition.objects.create(title="Src")
        self.targets = [Edition.objects.create(title=f"T{i}") for i in range(4)]
        for ident in strangers:
            for item in [self.src, *self.targets]:
                _public_mark(ident, item)
        BuildItemSimilarity().run()
        self.user = User.register(email="rft@t.com", username="rft")
        _public_mark(self.user.identity, self.src)
        # a stored list that the member has since used up
        _cached_rows(self.user, self.targets[:1])
        dismiss_item(self.user, self.targets[0])

    def _ids(self) -> set[int]:
        return {i.pk for i in for_you(self.user, limit=3)}

    def test_used_up_list_is_recomputed(self):
        assert self._ids() == {t.pk for t in self.targets[1:]}

    def test_refill_runs_once_a_day(self, monkeypatch):
        calls = []
        real = compute_for_user

        def counting(user_pk: int, identity_pk: int):
            calls.append(user_pk)
            return real(user_pk, identity_pk)

        monkeypatch.setattr("catalog.recommendation.compute_for_user", counting)
        self._ids()
        for t in self.targets[1:]:
            dismiss_item(self.user, t)
        assert self._ids() == set()
        assert calls == [self.user.pk]

    def test_nothing_new_keeps_the_stored_rows(self, monkeypatch):
        monkeypatch.setattr("catalog.recommendation.compute_for_user", lambda u, i: [])
        assert self._ids() == set()
        assert UserRecommendation.objects.filter(user=self.user).count() == 1

    def test_short_list_without_skips_is_left_alone(self, monkeypatch):
        restore_item(self.user, self.targets[0])
        monkeypatch.setattr(
            "catalog.recommendation.compute_for_user",
            lambda u, i: pytest.fail("recomputed a list nothing was taken from"),
        )
        assert self._ids() == {self.targets[0].pk}


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

    def test_restore_through_the_merged_item_clears_the_dismissal(self):
        old = Edition.objects.create(title="Old")
        old.merge_to(self.books[0])
        dismiss_item(self.user, old)
        restore_item(self.user, old)
        assert not RecommendationDismissal.objects.filter(user=self.user).exists()

    def test_dismissal_follows_a_merge_chain(self):
        a = Edition.objects.create(title="A")
        b = Edition.objects.create(title="B")
        dismiss_item(self.user, a)
        a.merge_to(b)
        b.merge_to(self.books[0])
        assert self.books[0].pk not in self._ids()
        restore_item(self.user, self.books[0])
        assert not RecommendationDismissal.objects.filter(user=self.user).exists()

    def test_dismissal_is_per_user(self):
        other = User.register(email="fy2@t.com", username="fy2")
        _cached_rows(other, self.books)
        dismiss_item(other, self.books[0])
        assert self.books[0].pk in self._ids()


class TestSimilarItems:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        strangers = [
            User.register(email=f"si{i}@t.com", username=f"si{i}").identity
            for i in range(2)
        ]
        self.src = Edition.objects.create(title="Src")
        self.t1 = Edition.objects.create(title="T1")
        self.t2 = Edition.objects.create(title="T2")
        for ident in strangers:
            for item in (self.src, self.t1, self.t2):
                _public_mark(ident, item)
        BuildItemSimilarity().run()
        self.user = User.register(email="sit@t.com", username="sit")

    def test_dismissed_item_is_not_similar(self):
        dismiss_item(self.user, self.t1)
        ids = {i.pk for i in similar_items(self.src, viewer=self.user)}
        assert self.t1.pk not in ids
        assert self.t2.pk in ids


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

    def test_list_layout_dismiss_and_undo_swap_rows(self):
        dismiss = reverse("catalog:dismiss_recommendation", args=[self.book.uuid])
        restore = reverse("catalog:restore_recommendation", args=[self.book.uuid])
        row = self.client.post(dismiss, {"layout": "list"}).content.decode()
        assert '<article class="item-card">' in row
        assert restore in row
        row = self.client.post(restore, {"layout": "list"}).content.decode()
        assert not RecommendationDismissal.objects.exists()
        assert 'class="entity-sort item-card"' in row
        assert dismiss in row

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
        assert reverse("catalog:discover_for_you") in content
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


class TestSeeAllPages:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        site_config.reco_user_top_n = 100
        self.user = User.register(email="sa@t.com", username="sa")
        self.client = _client(self.user)

    def test_pages_need_login(self):
        for name in ("catalog:discover_for_you", "catalog:discover_from_circles"):
            assert Client().get(reverse(name)).status_code == 302

    def test_for_you_pages_through_every_stored_row_but_dismissed(self):
        books = [Edition.objects.create(title=f"All {i}") for i in range(30)]
        _cached_rows(self.user, books)
        dismiss_item(self.user, books[0])
        url = reverse("catalog:discover_for_you")
        first = self.client.get(url).content.decode()
        second = self.client.get(url + "?page=2").content.decode()
        assert first.count('class="entity-sort item-card"') == 20
        assert second.count('class="entity-sort item-card"') == 9
        for b in books[1:]:
            dismiss = reverse("catalog:dismiss_recommendation", args=[b.uuid])
            assert dismiss in first or dismiss in second
        assert books[0].uuid not in first + second

    def test_trending_page_lists_the_cached_shelf(self, monkeypatch):
        books = [Edition.objects.create(title=f"Trend {i}") for i in range(3)]
        monkeypatch.setattr(
            "catalog.views.view.cache.get",
            lambda key, default=None: books if key == "trending_book" else default,
        )
        content = self.client.get(
            reverse("catalog:discover_category", args=["book"])
        ).content.decode()
        for b in books:
            assert b.url in content
        assert "/dismiss" not in content

    def test_from_circles_lists_followee_marks(self):
        friend = User.register(email="sa2@t.com", username="sa2")
        self.user.identity.follow(friend.identity, True)
        books = [Edition.objects.create(title=f"Circle {i}") for i in range(2)]
        for b in books:
            _public_mark(friend.identity, b)
        content = self.client.get(
            reverse("catalog:discover_from_circles")
        ).content.decode()
        for b in books:
            assert reverse("catalog:dismiss_recommendation", args=[b.uuid]) in content

    def test_opted_out_member_sees_nothing(self):
        _cached_rows(self.user, [Edition.objects.create(title="Opted Out")])
        self.user.preference.disable_recommendations = True
        self.user.preference.save(update_fields=["disable_recommendations"])
        content = self.client.get(reverse("catalog:discover_for_you")).content.decode()
        assert "Opted Out" not in content
        assert "Nothing so far." in content
