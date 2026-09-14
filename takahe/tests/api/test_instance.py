import pytest
from django.core.cache import cache
from django.utils import timezone

from users.models import Identity


@pytest.fixture
def local_cache(settings):
    """Keep the stats cache in-process, whatever the deployment cache is."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()


@pytest.mark.django_db
def test_instance(api_client):
    response = api_client.get("/api/v1/instance").json()
    assert response["uri"] == "example.com"


@pytest.mark.django_db
def test_instance_user_count_excludes_deleted(
    api_client, identity_factory, local_cache
):
    # the stats fallback only runs on a cold cache, which is how a deleted
    # identity used to slip into user_count
    cache.delete("instance_info_stats")
    before = api_client.get("/api/v1/instance").json()["stats"]["user_count"]
    gone = identity_factory(username="gone")
    Identity.objects.filter(pk=gone.pk).update(deleted=timezone.now())
    cache.delete("instance_info_stats")
    after = api_client.get("/api/v1/instance").json()["stats"]["user_count"]
    assert after == before
