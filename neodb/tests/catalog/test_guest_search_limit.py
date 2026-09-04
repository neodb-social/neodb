from unittest.mock import patch

import pytest
from django.test import Client

from common.models import SiteConfig
from users.models import User

LIMIT = 3
# items, num_pages, count, facets, q
EMPTY_RESULT = ([], 500, 0, {}, "book")


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

    def test_guest_blocked_past_limit(self):
        response, mocked = self._guest_get(f"/search?q=book&page={LIMIT + 1}")
        assert response.status_code == 404
        # the gate must come before the index is queried, or it saves nothing
        mocked.assert_not_called()

    def test_guest_allowed_up_to_limit(self):
        response, __ = self._guest_get(f"/search?q=book&page={LIMIT}")
        assert response.status_code == 200

    def test_guest_pagination_stops_at_limit(self):
        response, __ = self._guest_get("/search?q=book&page=1")
        assert response.context["pagination"].end_page == LIMIT

    def test_user_not_limited(self):
        user = User.register(email="pager@example.com", username="pager")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        with patch("catalog.views.search.query_index", return_value=EMPTY_RESULT):
            response = client.get(f"/search?q=book&page={LIMIT + 1}")
        assert response.status_code == 200
        assert response.context["pagination"].end_page > LIMIT

    def test_guest_blocked_on_people_search(self):
        response = Client().get(f"/search?c=people&q=tolkien&page={LIMIT + 1}")
        assert response.status_code == 404
