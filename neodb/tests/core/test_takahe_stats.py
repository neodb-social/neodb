import pytest
from django.core.cache import cache
from django.utils import timezone

from takahe.jobs import TakaheStats
from users.models import APIdentity, User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture(autouse=True)
def _isolated_cache(settings):
    """Keep the stats cache in-process.

    The default cache is Redis, and xdist workers past the sixteenth share a
    database; tests/common/test_about_page.py writes the same key.
    """
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()


def _user_count() -> int:
    TakaheStats().run()
    return (cache.get("instance_info_stats") or {})["user_count"]


def test_counts_active_local_user():
    User.register(username="alice")
    assert _user_count() == 1


def test_skips_disabled_user():
    User.register(username="alice")
    bob = User.register(username="bob")
    bob.is_active = False
    bob.save(update_fields=["is_active"])
    assert _user_count() == 1


def test_skips_deleted_identity_without_user():
    User.register(username="alice")
    gone = User.register(username="gone")
    APIdentity.objects.filter(pk=gone.identity.pk).update(
        user=None, deleted=timezone.now()
    )
    assert _user_count() == 1


def test_skips_remote_identity():
    User.register(username="alice")
    APIdentity.objects.create(
        local=False, username="remote", domain_name="other.example"
    )
    assert _user_count() == 1


def test_nodeinfo_total_matches():
    User.register(username="alice")
    disabled = User.register(username="disabled")
    disabled.is_active = False
    disabled.save(update_fields=["is_active"])
    TakaheStats().run()
    assert cache.get("nodeinfo_usage")["users"]["total"] == 1
