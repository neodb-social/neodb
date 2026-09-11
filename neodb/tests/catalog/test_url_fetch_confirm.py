"""A pasted URL must not make the server fetch on a GET.

The header posts a url typed into the search box to `fetch_url`; every GET
gets a confirmation form first, signed in or not.
"""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from catalog.models import Movie
from users.models import User

from .test_fetch_lock import FakeCache, StubSite

URL = "https://example.com/unknown-thing"


def _get(client, url, item=None):
    fake_cache = FakeCache()
    with (
        patch(
            "catalog.views.search.SiteManager.get_site_by_url",
            return_value=StubSite(item),
        ),
        patch("catalog.search.utils.cache", fake_cache),
        patch("catalog.views.search.enqueue_fetch") as enqueue,
    ):
        return client.get(url), enqueue


def _post(client, data, item=None):
    fake_cache = FakeCache()
    with (
        patch(
            "catalog.views.search.SiteManager.get_site_by_url",
            return_value=StubSite(item),
        ),
        patch("catalog.search.utils.cache", fake_cache),
        patch("catalog.views.search.enqueue_fetch") as enqueue,
    ):
        return client.post(reverse("catalog:fetch_url"), data), enqueue


@pytest.mark.django_db(databases="__all__")
class TestAnonymousUrlFetchConfirm:
    def test_guest_gets_a_form_instead_of_a_fetch(self):
        response, enqueue = _get(Client(), f"/search?q={URL}")
        assert response.status_code == 200
        assert "fetch_confirm.html" in [t.name for t in response.templates]
        enqueue.assert_not_called()

    def test_confirm_form_posts_the_url_back(self):
        response, __ = _get(Client(), f"/search?q={URL}")
        content = response.content.decode()
        assert f'action="{reverse("catalog:fetch_url")}"' in content
        assert 'name="csrfmiddlewaretoken"' in content
        assert f'name="url" value="{URL}"' in content

    def test_existing_item_still_redirects_without_confirming(self):
        movie = Movie.objects.create(title="Already Here")
        response, enqueue = _get(Client(), f"/search?q={URL}", item=movie)
        assert response.status_code == 302
        assert response["Location"] == movie.url
        enqueue.assert_not_called()

    def test_people_search_confirms_too(self):
        """``people_search`` shares ``resolve_url_query``."""
        response, enqueue = _get(Client(), f"/search?c=people&q={URL}")
        assert "fetch_confirm.html" in [t.name for t in response.templates]
        enqueue.assert_not_called()

    def test_guest_post_starts_the_fetch(self):
        response, enqueue = _post(Client(), {"url": URL})
        assert response.status_code == 200
        assert "fetch_pending.html" in [t.name for t in response.templates]
        enqueue.assert_called_once()

    def test_post_without_a_url_is_rejected(self):
        response, enqueue = _post(Client(), {})
        assert response.status_code == 400
        enqueue.assert_not_called()

    def test_post_of_a_non_url_is_rejected(self):
        """``resolve_url_query`` returns None without a scheme."""
        response, enqueue = _post(Client(), {"url": "not a url"})
        assert response.status_code == 400
        enqueue.assert_not_called()

    def test_post_of_a_local_url_redirects_without_fetching(self):
        """A POST still takes the local-host branch."""
        local = f"https://{settings.SITE_DOMAINS[0]}/movie/abc"
        response, enqueue = _post(Client(), {"url": local})
        assert response.status_code == 302
        assert response["Location"] == local
        enqueue.assert_not_called()

    def test_get_on_the_fetch_route_is_not_allowed(self):
        assert Client().get(reverse("catalog:fetch_url")).status_code == 405


@pytest.mark.django_db(databases="__all__")
class TestSignedInUrlFetch:
    """Signing in does not skip the confirmation."""

    def _client(self, username="fetcher"):
        user = User.register(email=f"{username}@example.com", username=username)
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        return client

    def test_user_get_is_confirmed_too(self):
        response, enqueue = _get(self._client(), f"/search?q={URL}")
        assert response.status_code == 200
        assert "fetch_confirm.html" in [t.name for t in response.templates]
        enqueue.assert_not_called()

    def test_user_post_starts_the_fetch(self):
        response, enqueue = _post(self._client("poster"), {"url": URL})
        assert response.status_code == 200
        assert "fetch_pending.html" in [t.name for t in response.templates]
        enqueue.assert_called_once()
