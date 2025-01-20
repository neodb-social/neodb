from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        from catalog import api, models, sites  # noqa
        from catalog.models import init_catalog_audit_log, init_catalog_search_models
        from journal import models as journal_models  # noqa

        # register cron jobs
        from catalog.jobs import DiscoverGenerator, PodcastUpdater  # noqa

        init_catalog_search_models()
        init_catalog_audit_log()
