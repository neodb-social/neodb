from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from catalog.models import Movie
from catalog.search.utils import (
    FETCH_URL_LOCK_TTL,
    FETCH_URL_LOCK_TTL_DONE,
    get_actor_fetch_lock,
    get_fetch_lock,
    mark_fetch_completed,
)
from users.models import User


class FakeCache:
    """Cache double that records the timeout each key was set with."""

    def __init__(self):
        self.values = {}
        self.timeouts = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, timeout=None):
        self.values[key] = value
        self.timeouts[key] = timeout


class FakeUser:
    is_authenticated = True

    def __init__(self, id):
        self.id = id


class FakeAnonymous:
    is_authenticated = False


@pytest.fixture(autouse=True)
def _production_ttls(settings):
    """The lock collapses every TTL to 1s under DEBUG; pin it off so the
    real values are asserted."""
    settings.DEBUG = False


@pytest.fixture
def fake_cache():
    c = FakeCache()
    with patch("catalog.search.utils.cache", c):
        yield c


class TestFetchLockTtl:
    def test_url_lock_starts_short(self, fake_cache):
        url = "https://example.com/a"
        assert get_fetch_lock(FakeUser(1), url) is True
        assert fake_cache.timeouts[f"_fetch_lock:{url}"] == FETCH_URL_LOCK_TTL

    def test_completed_fetch_extends_url_lock(self, fake_cache):
        url = "https://example.com/a"
        get_fetch_lock(FakeUser(1), url)
        mark_fetch_completed(url)
        assert fake_cache.timeouts[f"_fetch_lock:{url}"] == FETCH_URL_LOCK_TTL_DONE

    def test_url_lock_blocks_a_different_user(self, fake_cache):
        """The url half of the lock is global, so a second user is refused
        even though their own actor slot is free."""
        url = "https://example.com/a"
        assert get_fetch_lock(FakeUser(1), url) is True
        assert get_fetch_lock(FakeUser(2), url) is False

    def test_failed_fetch_leaves_the_short_ttl(self, fake_cache):
        """No mark_fetch_completed call means the url is retryable in
        minutes rather than hours."""
        url = "https://example.com/a"
        get_fetch_lock(FakeUser(1), url)
        assert fake_cache.timeouts[f"_fetch_lock:{url}"] == FETCH_URL_LOCK_TTL


class TestActorFetchLock:
    def test_second_call_by_same_user_is_refused(self, fake_cache):
        user = FakeUser(1)
        assert get_actor_fetch_lock(user) is True
        assert get_actor_fetch_lock(user) is False

    def test_users_do_not_share_a_slot(self, fake_cache):
        assert get_actor_fetch_lock(FakeUser(1)) is True
        assert get_actor_fetch_lock(FakeUser(2)) is True

    def test_anonymous_callers_share_one_slot(self, fake_cache):
        assert get_actor_fetch_lock(FakeAnonymous()) is True
        assert get_actor_fetch_lock(FakeAnonymous()) is False

    def test_actor_lock_refuses_before_touching_the_url(self, fake_cache):
        user = FakeUser(1)
        assert get_fetch_lock(user, "https://example.com/a") is True
        assert get_fetch_lock(user, "https://example.com/b") is False
        assert "_fetch_lock:https://example.com/b" not in fake_cache.values


class StubSiteName:
    label = "Example"


class StubSite:
    SITE_NAME = StubSiteName()

    def __init__(self, item=None):
        self._item = item

    def get_item(self, allow_rematch=True):
        return self._item

    def get_resource(self):
        return None


@pytest.mark.django_db(databases="__all__")
class TestRefetchIsThrottled:
    """Regression: refetch used to skip get_fetch_lock entirely, so a POST
    loop could queue an unbounded number of fetch jobs."""

    def _client(self, username="editor"):
        user = User.register(email=f"{username}@example.com", username=username)
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        return client

    def _post(self, client, url, fake_cache, item=None):
        with (
            patch(
                "catalog.views.search.SiteManager.get_site_by_url",
                return_value=StubSite(item),
            ),
            patch("catalog.search.utils.cache", fake_cache),
            patch("catalog.views.search.enqueue_fetch") as enqueue,
        ):
            response = client.post(reverse("catalog:refetch"), {"url": url})
        return response, enqueue

    def test_second_refetch_of_same_url_is_not_queued(self):
        fake_cache = FakeCache()
        client = self._client()
        url = "https://example.com/refetch-me"

        response, enqueue = self._post(client, url, fake_cache)
        assert response.status_code == 200
        assert enqueue.call_count == 1

        response, enqueue = self._post(client, url, fake_cache)
        assert response.status_code == 200
        assert enqueue.call_count == 0

    def test_refused_refetch_does_not_log_the_action(self):
        fake_cache = FakeCache()
        client = self._client()
        movie = Movie.objects.create(title="Blocked Refetch")
        url = "https://example.com/refetch-logged"

        with patch.object(Movie, "log_action") as log_action:
            self._post(client, url, fake_cache, item=movie)
            assert log_action.call_count == 1
            self._post(client, url, fake_cache, item=movie)
            assert log_action.call_count == 1
