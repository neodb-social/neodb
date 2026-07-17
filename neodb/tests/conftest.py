import pytest
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
