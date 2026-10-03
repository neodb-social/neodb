from collections.abc import Sequence

import pytest
from django.test import Client
from django.urls import reverse

from catalog.jobs.discover import DiscoverGenerator
from catalog.jobs.recommendation import BuildItemSimilarity
from catalog.models import Album, Edition, Game, Item, Movie, UserRecommendation
from catalog.recommendation import (
    blended_for_discover,
    compute_for_user,
    for_you,
    from_your_circles,
    similar_items,
)
from common.models import SiteConfig
from common.models.lang import language_variants
from journal.models import Mark, ShelfType
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def site_config(monkeypatch):
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig, "system", SiteConfig.system.model_copy(deep=True))
    sys = SiteConfig.system
    sys.discover_user_languages = True
    sys.min_marks_for_discover = 0
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


def _book(title: str, lang: str, language: list[str] | None = None) -> Edition:
    """An edition titled in ``lang``, and written in it unless ``language``."""
    return Edition.objects.create(
        title=title,
        localized_title=[{"lang": lang, "text": title}],
        language=[lang.split("-")[0]] if language is None else language,
    )


def _set_metadata(item: Item, metadata: dict) -> Item:
    Item.objects.filter(pk=item.pk).update(metadata=metadata)
    return Item.objects.get(pk=item.pk)


def _public_mark(identity, item) -> None:
    Mark(identity, item).update(ShelfType.COMPLETE, "", 8, [], 0)


def _member(username: str, languages: list[str] | None = None) -> User:
    user = User.register(email=f"{username}@example.com", username=username)
    if languages is not None:
        user.preference.catalog_languages = languages
        user.preference.save(update_fields=["catalog_languages"])
    return user


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def _stored_rows(user: User, items: Sequence[Item]) -> None:
    UserRecommendation.objects.bulk_create(
        UserRecommendation(user=user, item=item, score=len(items) - n, category="book")
        for n, item in enumerate(items)
    )


class TestMatching:
    def test_variants_cover_locales_and_spoken_languages(self):
        codes = language_variants(["zh", "en"])
        assert codes[0] == "zh"
        assert {"zh-cn", "zh-tw", "zh-hans", "zh-hant", "cmn", "yue"} <= set(codes)
        assert "en" in codes
        assert language_variants(["ja"]) == ["ja"]

    def test_own_language_or_title_language_matches(self):
        de_title = [{"lang": "de", "text": "Ein Buch"}]
        fits = [
            _book("An English Title", "en"),
            _book("一本日本小说", "zh-cn", ["ja"]),
            _book("A Translated Title", "en", ["de"]),
            _book("Ohne Sprache", "de", []),
            _book("Unbekannt", "de", ["x"]),
            _book("Teils unbekannt", "de", ["de", "x"]),
            _set_metadata(_book("Null", "de"), {"localized_title": de_title}),
            _set_metadata(
                _book("Null", "de"), {"localized_title": de_title, "language": None}
            ),
            _set_metadata(_book("Leer", "de"), {}),
        ]
        misses = [_book("Ein Buch", "de"), _book("Ein Werk", "de", ["de", "fr"])]
        codes = language_variants(["en", "ja"])
        for item in fits:
            assert item.in_languages(set(codes)), item.metadata
        for item in misses:
            assert not item.in_languages(set(codes)), item.metadata
        matched = set(
            Item.objects.filter(pk__in=[i.pk for i in fits + misses])
            .filter(Item.q_in_languages(codes))
            .values_list("pk", flat=True)
        )
        assert matched == {i.pk for i in fits}

    def test_null_metadata_fits_everyone(self):
        book = _book("Kein Metadata", "de")
        Item.objects.filter(pk=book.pk).update(metadata=None)
        assert (
            Item.objects.filter(pk=book.pk).filter(Item.q_in_languages(["en"])).exists()
        )

    def test_chinese_spoken_language_matches_zh(self):
        film = Movie.objects.create(
            title="Film", localized_title=[{"lang": "en", "text": "Film"}]
        )
        film.language = ["yue"]
        film.save()
        assert film.in_languages(set(language_variants(["zh"])))

    def test_codes_follow_the_site_option(self, site_config):
        user = _member("gate", ["en"])
        assert user.preference.catalog_language_codes() == ["en"]
        site_config.discover_user_languages = False
        assert user.preference.catalog_language_codes() == []
        site_config.discover_user_languages = True
        user.preference.catalog_languages = []
        assert user.preference.catalog_language_codes() == []


class TestForYou:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.user = _member("fy_lang", ["en"])
        self.en = [_book(f"English {i}", "en") for i in range(2)]
        self.zh = [_book(f"中文 {i}", "zh-cn") for i in range(2)]

    def _ids(self, limit: int) -> list[int]:
        return [i.pk for i in for_you(self.user, limit=limit)]

    def test_stored_rows_out_of_language_are_skipped(self):
        _stored_rows(self.user, [self.zh[0], self.en[0], self.zh[1], self.en[1]])
        assert self._ids(limit=2) == [self.en[0].pk, self.en[1].pk]

    def test_items_without_a_known_language_are_kept(self):
        unknown = _book("Sans langue", "fr", [])
        _stored_rows(self.user, [self.zh[0], unknown, self.en[0]])
        assert self._ids(limit=2) == [unknown.pk, self.en[0].pk]

    def test_every_language_without_the_site_option(self, site_config):
        site_config.discover_user_languages = False
        _stored_rows(self.user, [self.zh[0], self.en[0]])
        assert self._ids(limit=2) == [self.zh[0].pk, self.en[0].pk]

    def test_stored_rows_out_of_language_are_refilled(self, monkeypatch):
        _stored_rows(self.user, self.zh)
        calls = []

        def compute(user_pk: int, identity_pk: int) -> list[UserRecommendation]:
            calls.append(user_pk)
            return [
                UserRecommendation(user_id=user_pk, item=b, score=1, category="book")
                for b in self.en
            ]

        monkeypatch.setattr("catalog.recommendation.compute_for_user", compute)
        assert set(self._ids(limit=3)) == {b.pk for b in self.en}
        assert calls == [self.user.pk]


class TestGameAndAlbumLanguage:
    def test_empty_by_default_and_shown_when_set(self):
        game = Game.objects.create(title="A Game")
        album = Album.objects.create(title="An Album")
        assert game.language == []
        assert album.language == []
        assert game.in_languages({"en"})
        game.language = ["ja"]
        game.save()
        album.language = ["yue"]
        album.save()
        assert not Item.objects.get(pk=game.pk).in_languages({"en"})
        assert "Japanese" in Client().get(game.url).content.decode()
        assert "Yue Chinese" in Client().get(album.url).content.decode()

    def test_language_is_in_the_api_and_edit_form(self):
        game = Game.objects.create(title="A Game", language=["ja"])
        data = Client().get(f"/api/game/{game.uuid}").json()
        assert data["language"] == ["ja"]
        assert "language" in Game.METADATA_COPY_LIST
        assert "language" in Album.METADATA_COPY_LIST


class TestComputeAndSimilar:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        strangers = [_member(f"cs{i}").identity for i in range(3)]
        self.src = _book("Source", "en")
        self.zh = [_book(f"相似 {i}", "zh-cn") for i in range(3)]
        self.en = _book("Similar", "en")
        for ident in strangers:
            for item in (self.src, *self.zh):
                _public_mark(ident, item)
        # fewer marks, so it ranks below every Chinese title
        for ident in strangers[:2]:
            _public_mark(ident, self.en)
        BuildItemSimilarity().run()
        self.user = _member("cs_user", ["en"])
        _public_mark(self.user.identity, self.src)

    def _computed(self) -> set[int]:
        return {
            r.item_id for r in compute_for_user(self.user.pk, self.user.identity.pk)
        }

    def test_compute_keeps_to_the_member_languages(self, site_config):
        assert self._computed() == {self.en.pk}
        site_config.discover_user_languages = False
        assert self._computed() == {self.en.pk, *(b.pk for b in self.zh)}

    def test_similar_reads_past_the_out_of_language_rows(self):
        assert [i.pk for i in similar_items(self.src, self.user, limit=1)] == [
            self.en.pk
        ]

    def test_similar_for_anonymous_is_not_filtered(self):
        out = {i.pk for i in similar_items(self.src, None, limit=10)}
        assert out == {self.en.pk, *(b.pk for b in self.zh)}


class TestCirclesAreNotFiltered:
    def test_followee_marks_in_any_language(self, site_config):
        viewer = _member("circ_viewer", ["en"])
        friend = _member("circ_friend")
        viewer.identity.follow(friend.identity, True)
        book = _book("朋友的书", "zh-cn")
        _public_mark(friend.identity, book)
        assert [i.pk for i in from_your_circles(viewer)] == [book.pk]
        assert [i.pk for i in blended_for_discover(viewer)] == [book.pk]


class TestDiscover:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        reader = _member("disc_reader")
        self.en = _book("English Trending", "en")
        self.zh = _book("中文热门", "zh-cn")
        for book in (self.en, self.zh):
            _public_mark(reader.identity, book)
        DiscoverGenerator().run()

    def test_shelves_follow_each_member_languages(self):
        english = _client(_member("disc_en", ["en"])).get("/discover/")
        content = english.content.decode()
        assert "English Trending" in content
        assert "中文热门" not in content
        # rendered sections are cached; others must not get the filtered ones
        for client in (_client(_member("disc_all")), Client()):
            content = client.get("/discover/").content.decode()
            assert "English Trending" in content
            assert "中文热门" in content

    def test_member_languages_need_the_site_option(self, site_config):
        site_config.discover_user_languages = False
        content = _client(_member("disc_off", ["en"])).get("/discover/")
        assert "中文热门" in content.content.decode()

    def test_trending_page_is_filtered(self):
        url = reverse("catalog:discover_category", args=["book"])
        content = _client(_member("disc_page", ["zh"])).get(url).content.decode()
        assert self.zh.url in content
        assert self.en.url not in content


class TestPreferences:
    @pytest.fixture(autouse=True)
    def setup(self, site_config):
        self.user = _member("pref_lang", ["ja"])
        self.client = _client(self.user)
        self.url = reverse("users:preferences")

    def _save(self, languages: list[str]) -> None:
        self.client.post(self.url, {"catalog_languages": languages})
        self.user.preference.refresh_from_db()

    def test_saves_known_languages_and_drops_stored_rows(self):
        _stored_rows(self.user, [_book("Stored", "ja")])
        assert "catalog_languages" in self.client.get(self.url).content.decode()
        self._save(["en", "zh", "not-a-language"])
        assert self.user.preference.catalog_languages == ["en", "zh"]
        assert not UserRecommendation.objects.filter(user=self.user).exists()

    def test_unchanged_languages_keep_stored_rows(self):
        _stored_rows(self.user, [_book("Stored", "ja")])
        self._save(["ja"])
        assert UserRecommendation.objects.filter(user=self.user).exists()

    def test_hidden_select_keeps_the_stored_list(self, site_config):
        site_config.discover_user_languages = False
        assert "catalog_languages" not in self.client.get(self.url).content.decode()
        self._save([])
        assert self.user.preference.catalog_languages == ["ja"]

    def test_api_returns_the_languages(self):
        app = Takahe.get_or_create_app(
            "Catalog Language Tests",
            "https://example.org",
            "https://example.org/callback",
            owner_pk=self.user.identity.pk,
        )
        token = Takahe.refresh_token(app, self.user.identity.pk, self.user.pk)
        data = Client().get(
            "/api/me/preference", headers={"Authorization": f"Bearer {token}"}
        )
        assert data.json()["catalog_languages"] == ["ja"]
