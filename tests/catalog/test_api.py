from unittest.mock import patch

import pytest
import requests
from django.core.cache import cache

from catalog.models import Performance


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_trending_performance_endpoint(live_server):
    with patch("catalog.models.item.Item.update_index"):
        performance = Performance.objects.create(title="Test Performance")

    cache.set("trending_performance", [performance], timeout=None)

    response = requests.get(f"{live_server.url}/api/trending/performance/", timeout=5)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["uuid"] == performance.uuid
    assert payload[0]["category"] == "performance"
    assert payload[0]["display_title"] == "Test Performance"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_trending_tag_endpoint(live_server):
    cache.set("popular_tags", ["speculative", "noir"], timeout=None)

    response = requests.get(f"{live_server.url}/api/trending/tag/", timeout=5)

    assert response.status_code == 200
    payload = response.json()
    assert payload == ["speculative", "noir"]
