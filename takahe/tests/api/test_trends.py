import pytest
from django.core.cache import cache


@pytest.mark.django_db
def test_trends_statuses_keep_cached_rank(api_client):
    ids = [
        api_client.post(
            "/api/v1/statuses",
            content_type="application/json",
            data={"status": f"Trend {i}.", "visibility": "public"},
        ).json()["id"]
        for i in range(3)
    ]
    ranked = [ids[1], ids[0], ids[2]]
    cache.set("trends_statuses", [int(i) for i in ranked])
    try:
        response = api_client.get("/api/v1/trends/statuses").json()
        assert [s["id"] for s in response] == ranked

        response = api_client.get("/api/v1/trends/statuses?limit=1&offset=1").json()
        assert [s["id"] for s in response] == [ids[0]]
    finally:
        cache.delete("trends_statuses")
