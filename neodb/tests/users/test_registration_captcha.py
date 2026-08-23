import io
import json
from typing import cast

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Album, Game, ItemCategory, Movie, Podcast, PodcastEpisode
from common.models import SiteConfig
from journal.models import Mark, ShelfType
from mastodon.models import Email
from users import registration_captcha as captcha
from users.jobs.captcha_pool import RegistrationCaptchaPool
from users.models import User

CAPTCHA_URL = reverse("users:captcha")
REGISTER_URL = reverse("users:register")
LOGIN_URL = reverse("users:login")


COVER_FILL = (120, 140, 160)  # mid-tone: unmistakable against any pad colour


def _cover_bytes(color: tuple[int, int, int] = COVER_FILL) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 90), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _with_cover(item, name: str):
    item.cover.save(name, ContentFile(_cover_bytes()), save=True)
    return item


def _make_items(count: int = 4) -> None:
    for i in range(count):
        _with_cover(Movie.objects.create(title=f"Movie {i}"), f"movie{i}.jpg")
        _with_cover(Album.objects.create(title=f"Album {i}"), f"album{i}.jpg")


def _human_trace(tokens) -> dict:
    """A trace that passes every check: off-line path, uneven sampling,
    distinct per-tile durations, and enough total interaction time."""
    trace = {}
    for i, token in enumerate(tokens):
        end = 400.0 + i * 37.0
        trace[token] = {
            "mode": "drag",
            "duration": end,
            "points": [
                [0.0, 0.0, 0.0],
                [10.5, 6.25, 30.0],
                [22.0, 9.0, 71.0],
                [40.0, 12.0, end],
            ],
        }
    return trace


def _verify_email(client: Client, email: str = "captcha@example.org") -> None:
    account = Email.new_account(email)
    assert account is not None
    session = client.session
    session["verified_account"] = account.to_dict()
    session.save()


def _challenge(client: Client) -> captcha.Challenge:
    return cast(captcha.Challenge, client.session[captcha.SESSION_KEY])


def _correct_answer(challenge: captcha.Challenge) -> dict:
    return {token: tile["row"] for token, tile in challenge["tiles"].items()}


def _submit(client: Client, challenge: captcha.Challenge, answer=None, trace=None):
    tokens = list(challenge["tiles"].keys())
    return client.post(
        CAPTCHA_URL,
        {
            "action": "submit",
            "nonce": challenge["nonce"],
            "answer": json.dumps(
                _correct_answer(challenge) if answer is None else answer
            ),
            "trace": json.dumps(_human_trace(tokens) if trace is None else trace),
        },
    )


def _wrong_answer(challenge: captcha.Challenge) -> dict:
    # flip one tile into the other row
    answer = _correct_answer(challenge)
    token = next(iter(answer))
    answer[token] = 1 - answer[token]
    return answer


def _drop_captcha_keys() -> None:
    """Drop only this feature's cache entries.

    Never cache.clear() here: the default cache is a Redis database shared with
    the rest of the suite (index aliases, site config, rate limits), so flushing
    it makes unrelated tests fail depending on the order they run in.
    """
    delete_pattern = getattr(cache, "delete_pattern", None)
    patterns = ("captcha_pool_*", "captcha_tile_*", "captcha_fail_open*")
    if delete_pattern:
        for pattern in patterns:
            delete_pattern(pattern)
        delete_pattern("reg_captcha_fails_*")
        return
    for category in ItemCategory:
        cache.delete(f"captcha_pool_popular_{category.value}")
        cache.delete(f"captcha_pool_all_{category.value}")
    cache.delete("captcha_fail_open")
    cache.delete("captcha_fail_open_warned")


@pytest.fixture(autouse=True)
def _clear_captcha_cache():
    _drop_captcha_keys()
    yield
    _drop_captcha_keys()


@pytest.fixture
def media_root(tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield tmp_path


def _configure(monkeypatch: pytest.MonkeyPatch, **updates) -> None:
    configured = SiteConfig.system.model_copy(update=updates)
    monkeypatch.setattr(SiteConfig, "system", configured)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch, media_root):
    _configure(
        monkeypatch,
        registration_captcha_items=4,
        min_marks_for_captcha=1,
        invite_only=False,
        mastodon_login_whitelist=[],
    )
    _make_items()


@pytest.mark.django_db(databases="__all__")
class TestDisabled:
    def test_default_is_off(self, client):
        assert SiteConfig.SystemOptions().registration_captcha_items == 0
        assert not captcha.is_enabled()

    def test_registration_untouched_when_disabled(self, client, monkeypatch):
        _configure(monkeypatch, registration_captcha_items=0)
        _verify_email(client)
        response = client.get(REGISTER_URL)
        assert response.status_code == 200

    def test_captcha_page_redirects_when_disabled(self, client, monkeypatch):
        _configure(monkeypatch, registration_captcha_items=0)
        _verify_email(client)
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 302
        assert response.url == REGISTER_URL


@pytest.mark.django_db(databases="__all__")
class TestGate:
    def test_get_register_redirects_to_captcha(self, client, enabled):
        _verify_email(client)
        response = client.get(REGISTER_URL)
        assert response.status_code == 302
        assert response.url == CAPTCHA_URL

    def test_direct_post_to_register_is_blocked(self, client, enabled):
        _verify_email(client)
        response = client.post(REGISTER_URL, {"username": "sneaky", "email": ""})
        assert response.status_code == 302
        assert response.url == CAPTCHA_URL
        assert not User.objects.filter(username="sneaky").exists()

    def test_captcha_requires_a_verified_identity(self, client, enabled):
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 302
        assert response.url == LOGIN_URL

    def test_authenticated_user_is_not_gated(self, client, enabled):
        # register() doubles as the email-change form for logged-in users
        user = User.register(email="already@example.org", username="already")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(REGISTER_URL)
        assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
class TestSolving:
    def test_happy_path(self, client, enabled):
        _verify_email(client)
        assert client.get(CAPTCHA_URL).status_code == 200
        response = _submit(client, _challenge(client))
        assert response.status_code == 302
        assert response.url == REGISTER_URL
        assert client.session[captcha.PASSED_KEY] is True

        response = client.post(REGISTER_URL, {"username": "newbie", "email": ""})
        assert response.status_code == 200
        assert User.objects.filter(username="newbie").exists()

    def test_passing_clears_captcha_keys_on_login(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        _submit(client, _challenge(client))
        client.post(REGISTER_URL, {"username": "cleared", "email": ""})
        assert captcha.PASSED_KEY not in client.session
        assert captcha.SESSION_KEY not in client.session

    def test_tiles_are_the_configured_total(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        assert len(challenge["tiles"]) == 4
        assert len(challenge["rows"]) == 2
        assert challenge["rows"][0] != challenge["rows"][1]

    def test_both_rows_are_used(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        rows = {tile["row"] for tile in _challenge(client)["tiles"].values()}
        assert rows == {0, 1}

    def test_get_is_idempotent(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        first = _challenge(client)
        client.get(CAPTCHA_URL)
        second = _challenge(client)
        assert first["tiles"] == second["tiles"]
        assert first["nonce"] == second["nonce"]
        assert first["started"] == second["started"]


@pytest.mark.django_db(databases="__all__")
class TestFailureBudget:
    def test_wrong_answer_consumes_a_regeneration(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        response = _submit(client, challenge, answer=_wrong_answer(challenge))
        assert response.status_code == 302
        assert response.url == CAPTCHA_URL
        after = _challenge(client)
        assert after["regens"] == 1
        assert after["nonce"] != challenge["nonce"]
        assert captcha.PASSED_KEY not in client.session

    def test_manual_regenerate_consumes_one(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        client.post(CAPTCHA_URL, {"action": "regenerate", "nonce": challenge["nonce"]})
        assert _challenge(client)["regens"] == 1

    def test_third_failure_ends_the_session(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        for _ in range(2):
            challenge = _challenge(client)
            _submit(client, challenge, answer=_wrong_answer(challenge))
        challenge = _challenge(client)
        assert challenge["regens"] == 2
        response = _submit(client, challenge, answer=_wrong_answer(challenge))
        assert response.status_code == 200
        assert "verified_account" not in client.session
        assert captcha.SESSION_KEY not in client.session

    def test_correct_answer_still_passes_with_no_regenerations_left(
        self, client, enabled
    ):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        for _ in range(2):
            challenge = _challenge(client)
            _submit(client, challenge, answer=_wrong_answer(challenge))
        challenge = _challenge(client)
        assert captcha.regenerations_left(challenge) == 0
        response = _submit(client, challenge)
        assert response.status_code == 302
        assert response.url == REGISTER_URL

    def test_regenerate_with_none_left_is_a_no_op(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        for _ in range(2):
            challenge = _challenge(client)
            _submit(client, challenge, answer=_wrong_answer(challenge))
        challenge = _challenge(client)
        response = client.post(
            CAPTCHA_URL, {"action": "regenerate", "nonce": challenge["nonce"]}
        )
        assert response.status_code == 302
        assert response.url == CAPTCHA_URL
        assert _challenge(client)["nonce"] == challenge["nonce"]
        assert "verified_account" in client.session

    def test_nonce_mismatch_consumes_nothing(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        response = client.post(
            CAPTCHA_URL,
            {
                "action": "submit",
                "nonce": "stale",
                "answer": json.dumps(_wrong_answer(challenge)),
                "trace": json.dumps(_human_trace(list(challenge["tiles"]))),
            },
        )
        assert response.status_code == 302
        after = _challenge(client)
        assert after["regens"] == 0
        assert after["nonce"] == challenge["nonce"]


@pytest.mark.django_db(databases="__all__")
class TestTimeBudget:
    def _age_challenge(self, client: Client, seconds: int) -> None:
        session = client.session
        challenge = session[captcha.SESSION_KEY]
        challenge["started"] -= seconds
        session[captcha.SESSION_KEY] = challenge
        session.save()

    def test_expiry_ends_the_session_even_when_correct(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        self._age_challenge(client, captcha.CAPTCHA_TTL + 1)
        response = _submit(client, challenge)
        assert response.status_code == 200
        assert "verified_account" not in client.session

    def test_expiry_on_get_ends_the_session(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        self._age_challenge(client, captcha.CAPTCHA_TTL + 1)
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 200
        assert "verified_account" not in client.session

    def test_regenerating_does_not_extend_the_budget(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        started = _challenge(client)["started"]
        self._age_challenge(client, 120)
        challenge = _challenge(client)
        client.post(CAPTCHA_URL, {"action": "regenerate", "nonce": challenge["nonce"]})
        assert _challenge(client)["started"] == started - 120


@pytest.mark.django_db(databases="__all__")
class TestTrajectory:
    def test_collinear_path_is_rejected(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {
                "mode": "drag",
                "duration": 500.0 + i,
                # perfectly interpolated: what naive automation produces
                "points": [[0.0, 0.0, 0.0], [10.0, 10.0, 40.0], [20.0, 20.0, 90.0]],
            }
            for i, token in enumerate(challenge["tiles"])
        }
        _submit(client, challenge, trace=trace)
        assert _challenge(client)["regens"] == 1

    def test_instant_submission_is_rejected(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {
                "mode": "drag",
                "duration": 1.0 + i,
                "points": [[0.0, 0.0, 0.0], [5.0, 9.0, 1.0], [20.0, 20.0, 2.0]],
            }
            for i, token in enumerate(challenge["tiles"])
        }
        _submit(client, challenge, trace=trace)
        assert _challenge(client)["regens"] == 1

    def test_single_sample_drag_is_rejected(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {"mode": "drag", "duration": 900.0 + i, "points": [[1.0, 1.0, 0.0]]}
            for i, token in enumerate(challenge["tiles"])
        }
        _submit(client, challenge, trace=trace)
        assert _challenge(client)["regens"] == 1

    def test_missing_trace_entry_is_rejected(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = _human_trace(list(challenge["tiles"]))
        trace.pop(next(iter(trace)))
        _submit(client, challenge, trace=trace)
        assert _challenge(client)["regens"] == 1

    def test_oversized_payload_is_rejected(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        response = client.post(
            CAPTCHA_URL,
            {
                "action": "submit",
                "nonce": challenge["nonce"],
                "answer": json.dumps(_correct_answer(challenge)),
                "trace": "x" * (captcha.CAPTCHA_PAYLOAD_MAX_LENGTH + 1),
            },
        )
        assert response.status_code == 302
        assert _challenge(client)["regens"] == 1

    def test_kill_switch_lets_a_flat_trace_through(self, client, enabled, monkeypatch):
        monkeypatch.setattr(captcha, "REQUIRE_HUMAN_TRAJECTORY", False)
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {"mode": "drag", "duration": 0.0, "points": [[0.0, 0.0, 0.0]]}
            for token in challenge["tiles"]
        }
        response = _submit(client, challenge, trace=trace)
        assert response.status_code == 302
        assert response.url == REGISTER_URL

    def test_click_mode_passes_above_the_dwell_floor(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {"mode": "click", "duration": 500.0 + i * 20, "points": []}
            for i, token in enumerate(challenge["tiles"])
        }
        response = _submit(client, challenge, trace=trace)
        assert response.status_code == 302
        assert response.url == REGISTER_URL

    def test_click_mode_fails_below_the_dwell_floor(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        trace = {
            token: {"mode": "click", "duration": 1.0 + i, "points": []}
            for i, token in enumerate(challenge["tiles"])
        }
        _submit(client, challenge, trace=trace)
        assert _challenge(client)["regens"] == 1

    def test_wrong_answer_beats_a_perfect_trace(self, client, enabled):
        # correctness is checked first: good telemetry never rescues a bad sort
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        _submit(client, challenge, answer=_wrong_answer(challenge))
        assert captcha.PASSED_KEY not in client.session


@pytest.mark.django_db(databases="__all__")
class TestLeakage:
    def test_page_reveals_no_titles_ids_or_categories(self, client, enabled):
        _verify_email(client)
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 200
        body = response.content.decode()
        challenge = _challenge(client)

        from catalog.models import Item

        for tile in challenge["tiles"].values():
            item = Item.objects.get(pk=tile["pk"])
            assert item.display_title not in body
            assert str(item.uuid) not in body
            assert item.url not in body
        # the only category words on the page are the two row labels
        labels = {str(ItemCategory(v).label) for v in challenge["rows"]}
        for category in ItemCategory:
            if str(category.label) in labels:
                continue
            assert f">{category.label}<" not in body

    def test_every_image_goes_through_the_proxy(self, client, enabled):
        _verify_email(client)
        response = client.get(CAPTCHA_URL)
        body = response.content.decode()
        challenge = _challenge(client)
        for token in challenge["tiles"]:
            assert reverse("users:captcha_tile", args=[token]) in body
        # a raw media cover URL would carry item/<category>/ in the path
        assert "/m/item/" not in body


@pytest.mark.django_db(databases="__all__")
class TestTileProxy:
    def test_serves_a_uniform_square(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        sizes = set()
        for token in challenge["tiles"]:
            response = client.get(reverse("users:captcha_tile", args=[token]))
            assert response.status_code == 200
            assert response["Content-Type"] == captcha.CAPTCHA_TILE_CONTENT_TYPE
            assert response["Cache-Control"] == "no-store, private"
            assert "Cookie" in response["Vary"]
            image = Image.open(io.BytesIO(response.content))
            sizes.add(image.size)
        assert sizes == {(captcha.CAPTCHA_TILE_PX, captcha.CAPTCHA_TILE_PX)}

    def test_tile_is_full_bleed_so_shape_cannot_be_measured(self, client, enabled):
        """A non-square cover must not come back with padding bars.

        Fitting-and-padding would keep every tile the same pixel size while
        leaving the original aspect ratio trivially recoverable from the
        bounding box of the flat bars -- and aspect ratio is close to a
        giveaway for the category (square album art, 2:3 posters, tall book
        covers). The cover has to reach all four edges.
        """
        _verify_email(client)
        client.get(CAPTCHA_URL)
        for token in _challenge(client)["tiles"]:
            response = client.get(reverse("users:captcha_tile", args=[token]))
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            w, h = image.size
            corners = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)]
            for xy in corners:
                pixel = image.getpixel(xy)
                assert isinstance(pixel, tuple)
                # the source is a solid COVER_FILL rectangle, so every corner
                # is cover -- a padding bar would read as black or white
                drift = max(abs(a - b) for a, b in zip(pixel, COVER_FILL))
                assert drift < 24, f"corner {xy} is not cover: {pixel}"

    def test_unknown_token_404s(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        response = client.get(reverse("users:captcha_tile", args=["nope"]))
        assert response.status_code == 404

    def test_stale_token_404s(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        stale = next(iter(challenge["tiles"]))
        client.post(CAPTCHA_URL, {"action": "regenerate", "nonce": challenge["nonce"]})
        assert (
            client.get(reverse("users:captcha_tile", args=[stale])).status_code == 404
        )

    def test_another_sessions_token_404s(self, client, enabled):
        _verify_email(client)
        client.get(CAPTCHA_URL)
        token = next(iter(_challenge(client)["tiles"]))
        other = Client()
        assert other.get(reverse("users:captcha_tile", args=[token])).status_code == 404


@pytest.mark.django_db(databases="__all__")
class TestPool:
    def test_covers_are_required(self, client, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        _make_items()
        naked = Movie.objects.create(title="No Cover At All")
        assert naked.pk not in captcha.build_pool(ItemCategory.Movie, popular=False)

    def test_default_cover_is_not_a_cover(self, client, monkeypatch, media_root):
        from django.conf import settings

        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        placeholder = Movie.objects.create(
            title="Placeholder", cover=settings.DEFAULT_ITEM_COVER
        )
        assert placeholder.pk not in captcha.build_pool(
            ItemCategory.Movie, popular=False
        )

    def test_performance_is_excluded(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4)
        assert ItemCategory.Performance not in captcha.eligible_categories()

    def test_hidden_categories_are_excluded(self, monkeypatch, media_root):
        _configure(
            monkeypatch, registration_captcha_items=4, hidden_categories=["game"]
        )
        assert ItemCategory.Game not in captcha.eligible_categories()

    def test_only_available_categories_are_eligible(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4)
        eligible = captcha.eligible_categories()
        assert ItemCategory.People not in eligible
        assert ItemCategory.Collection not in eligible

    def test_episode_classes_are_excluded(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        podcast = _with_cover(Podcast.objects.create(title="Show"), "show.jpg")
        episode = _with_cover(
            PodcastEpisode.objects.create(
                title="Ep 1", program=podcast, pub_date=timezone.now()
            ),
            "ep.jpg",
        )
        pool = captcha.build_pool(ItemCategory.Podcast, popular=False)
        assert podcast.pk in pool
        assert episode.pk not in pool

    def test_marks_threshold_filters_the_popular_pool(
        self, client, monkeypatch, media_root
    ):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        movie = _with_cover(Movie.objects.create(title="Marked"), "marked.jpg")
        user = User.register(email="marker@example.org", username="marker")
        Mark(user.identity, movie).update(ShelfType.WISHLIST)
        assert movie.pk in captcha.build_pool(ItemCategory.Movie, popular=True)

        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=5)
        assert movie.pk not in captcha.build_pool(ItemCategory.Movie, popular=True)

    def test_pool_is_capped(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        monkeypatch.setattr(captcha, "CAPTCHA_POOL_LIMIT", 3)
        for i in range(5):
            _with_cover(Game.objects.create(title=f"Game {i}"), f"game{i}.jpg")
        assert len(captcha.build_pool(ItemCategory.Game, popular=False)) == 3

    def test_fail_open_on_a_thin_catalog(self, client, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        # only one category has anything to show
        _with_cover(Movie.objects.create(title="Lonely"), "lonely.jpg")
        _verify_email(client)
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 302
        assert response.url == REGISTER_URL
        assert client.session[captcha.PASSED_KEY] is True
        assert client.post(REGISTER_URL, {"username": "thin", "email": ""})
        assert User.objects.filter(username="thin").exists()


@pytest.mark.django_db(databases="__all__")
class TestRateLimit:
    def test_cap_blocks_new_challenges(self, client, enabled, monkeypatch):
        monkeypatch.setattr(captcha, "CAPTCHA_MAX_FAILS", 1)
        _verify_email(client)
        client.get(CAPTCHA_URL)
        challenge = _challenge(client)
        _submit(client, challenge, answer=_wrong_answer(challenge))
        # drop the live challenge so the next GET has to issue one
        session = client.session
        del session[captcha.SESSION_KEY]
        session.save()
        response = client.get(CAPTCHA_URL)
        assert response.status_code == 200
        assert captcha.SESSION_KEY not in client.session

    def test_counter_survives_repeated_failures(self):
        captcha.record_fail("203.0.113.7")
        captcha.record_fail("203.0.113.7")
        assert cache.get("reg_captcha_fails_203.0.113.7") == 2
        assert not captcha.fails_exceeded("203.0.113.7")
        assert not captcha.fails_exceeded("")


@pytest.mark.django_db(databases="__all__")
class TestVerificationResetsState:
    def test_new_verified_identity_clears_a_solved_captcha(self, client, enabled):
        from mastodon.views.common import register_new_user

        _verify_email(client)
        client.get(CAPTCHA_URL)
        _submit(client, _challenge(client))
        assert client.session[captcha.PASSED_KEY] is True

        # a second verification must start the step over
        request = client.request().wsgi_request
        request.session = client.session
        account = Email.new_account("second@example.org")
        assert account is not None
        register_new_user(request, account)
        assert captcha.PASSED_KEY not in request.session
        assert captcha.SESSION_KEY not in request.session


@pytest.mark.django_db(databases="__all__")
class TestSettings:
    def test_too_few_items_is_rejected(self):
        for value in (1, 2, 3):
            with pytest.raises(ValueError):
                SiteConfig.SystemOptions(registration_captcha_items=value)

    def test_zero_and_sane_values_are_accepted(self):
        assert SiteConfig.SystemOptions(registration_captcha_items=0)
        for value in (4, 5, 6, 8):
            assert (
                SiteConfig.SystemOptions(
                    registration_captcha_items=value
                ).registration_captcha_items
                == value
            )

    def test_too_many_items_is_rejected(self):
        with pytest.raises(ValueError):
            SiteConfig.SystemOptions(registration_captcha_items=99)

    def test_min_marks_must_be_positive(self):
        with pytest.raises(ValueError):
            SiteConfig.SystemOptions(min_marks_for_captcha=0)


@pytest.mark.django_db(databases="__all__")
class TestPoolJob:
    def test_no_op_when_disabled(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=0)
        _make_items()
        RegistrationCaptchaPool().run()
        assert cache.get("captcha_pool_all_movie") is None

    def test_populates_every_eligible_category(self, monkeypatch, media_root):
        _configure(monkeypatch, registration_captcha_items=4, min_marks_for_captcha=1)
        _make_items()
        RegistrationCaptchaPool().run()
        for category in captcha.eligible_categories():
            assert cache.get(f"captcha_pool_all_{category.value}") is not None
            assert cache.get(f"captcha_pool_popular_{category.value}") is not None
        assert len(cache.get("captcha_pool_all_movie")) == 4
