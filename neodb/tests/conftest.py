import pytest
from django.core.cache import cache
from django.utils import translation

# Marker for multi-database tests - equivalent to Django's databases = "__all__"
pytest.mark.all_databases = pytest.mark.django_db(databases="__all__", transaction=True)  # type: ignore


@pytest.fixture(autouse=True)
def _reset_language():
    """Reset the active language after each test.

    Code paths like activate_language_for_user (middleware, crosspost
    jobs) call translation.activate without restoring, which leaks the
    language into later tests and makes failures depend on test order.
    """
    yield
    translation.deactivate()


@pytest.fixture(autouse=True)
def _clear_circles_cache():
    """Drop cached circles rows keyed by user pk, and rendered discover sections.

    Tests share redis with the dev cluster and --create-db restarts the pk
    sequences, so a key left by an earlier run can match a new test user.
    Discover sections are keyed by language and rotation slot only, so one
    test's shelves would show up in the next.
    """
    delete_pattern = getattr(cache, "delete_pattern", None)
    if delete_pattern:
        delete_pattern("reco:circles:*")
        delete_pattern("discover_frag:*")


@pytest.fixture(autouse=True)
def _load_site_config():
    """Ensure SiteConfig is loaded for all tests."""
    from common.models.site_config import SiteConfig

    if not getattr(SiteConfig, "system", None):
        try:
            SiteConfig.ensure_loaded()
        except RuntimeError:
            # DB not available (test not marked with django_db)
            # Use env defaults via Pydantic
            SiteConfig.system = SiteConfig.SystemOptions()
