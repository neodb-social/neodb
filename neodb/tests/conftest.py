import pytest

# Marker for multi-database tests - equivalent to Django's databases = "__all__"
pytest.mark.all_databases = pytest.mark.django_db(databases="__all__", transaction=True)  # type: ignore


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
