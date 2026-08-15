from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from users.models import User


class FakeCache:
    """Cache double so the throttle is exercised without touching redis."""

    def __init__(self):
        self.values = {}
        self.timeouts = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, timeout=None):
        self.values[key] = value
        self.timeouts[key] = timeout


@pytest.fixture(autouse=True)
def _production_ttls(settings):
    """The lock collapses every TTL to 1s under DEBUG."""
    settings.DEBUG = False


@pytest.mark.django_db(databases="__all__")
class TestQueryIdentityIsThrottled:
    """Each miss makes Takahe send a signed request to an arbitrary domain,
    so the search box cannot be used as an unthrottled outbound fan-out."""

    def _search(self, client, handle, fake_cache):
        with (
            patch("catalog.search.utils.cache", fake_cache),
            patch("users.views.actions.Takahe.fetch_remote_identity") as fetch,
        ):
            response = client.get(reverse("common:search"), {"q": handle})
        return response, fetch

    def test_anonymous_callers_share_one_slot(self):
        fake_cache = FakeCache()

        response, fetch = self._search(Client(), "@alice@example.com", fake_cache)
        assert response.status_code == 200
        assert fetch.call_count == 1

        # A different anonymous caller and a different handle: still refused,
        # because unauthenticated traffic shares a single slot.
        response, fetch = self._search(Client(), "@bob@example.org", fake_cache)
        assert response.status_code == 200
        assert fetch.call_count == 0

    def test_second_query_by_same_user_is_refused(self):
        fake_cache = FakeCache()
        user = User.register(email="searcher@example.com", username="searcher")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        _, fetch = self._search(client, "@alice@example.com", fake_cache)
        assert fetch.call_count == 1

        _, fetch = self._search(client, "@bob@example.org", fake_cache)
        assert fetch.call_count == 0

    def test_authenticated_user_has_their_own_slot(self):
        fake_cache = FakeCache()
        user = User.register(email="searcher2@example.com", username="searcher2")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        # An anonymous caller taking the shared slot must not block a
        # signed-in user.
        _, fetch = self._search(Client(), "@alice@example.com", fake_cache)
        assert fetch.call_count == 1
        _, fetch = self._search(client, "@bob@example.org", fake_cache)
        assert fetch.call_count == 1
