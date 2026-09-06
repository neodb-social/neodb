from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.test import Client

from common.models import SiteConfig
from users.models import User

LIMIT = 3
# items, num_pages, count, facets, q
EMPTY_RESULT = ([], 500, 0, {}, "book")


def assert_login_redirect(response, target: str) -> None:
    assert response.status_code == 302
    location = urlparse(response.url)
    assert location.path == settings.LOGIN_URL
    assert parse_qs(location.query)["next"] == [target]


@pytest.mark.django_db(databases="__all__")
class TestGuestSearchPageLimit:
    @pytest.fixture(autouse=True)
    def set_limit(self):
        old_system = getattr(SiteConfig, "system", None)
        SiteConfig.set_system(guest_search_max_pages=LIMIT)
        SiteConfig.reload()
        yield
        SiteConfig.objects.filter(pk=1).delete()
        if old_system is not None:
            SiteConfig.system = old_system
            SiteConfig._apply_to_settings(old_system)

    def _guest_get(self, url):
        with patch(
            "catalog.views.search.query_index", return_value=EMPTY_RESULT
        ) as mocked:
            return Client().get(url), mocked

    def test_guest_sent_to_login_past_limit(self):
        url = f"/search?q=book&page={LIMIT + 1}"
        response, mocked = self._guest_get(url)
        assert_login_redirect(response, url)
        # the gate must come before the index is queried, or it saves nothing
        mocked.assert_not_called()

    def test_guest_allowed_up_to_limit(self):
        response, __ = self._guest_get(f"/search?q=book&page={LIMIT}")
        assert response.status_code == 200

    def test_guest_pagination_shows_pages_past_limit(self):
        response, __ = self._guest_get("/search?q=book&page=1")
        assert response.context["pagination"].end_page > LIMIT

    def test_user_not_limited(self):
        user = User.register(email="pager@example.com", username="pager")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        with patch("catalog.views.search.query_index", return_value=EMPTY_RESULT):
            response = client.get(f"/search?q=book&page={LIMIT + 1}")
        assert response.status_code == 200
        assert response.context["pagination"].end_page > LIMIT

    @pytest.mark.parametrize("stored", [0, -1])
    def test_below_one_means_one(self, stored):
        SiteConfig.set_system(guest_search_max_pages=stored)
        SiteConfig.reload()
        assert SiteConfig.system.guest_search_max_pages == 1
        response, __ = self._guest_get("/search?q=book&page=2")
        assert_login_redirect(response, "/search?q=book&page=2")

    def test_guest_sent_to_login_on_people_search(self):
        url = f"/search?c=people&q=tolkien&page={LIMIT + 1}"
        response = Client().get(url)
        assert_login_redirect(response, url)
