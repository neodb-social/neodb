from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        from catalog import models
        from catalog import sites
        from journal import models as journal_models
        from catalog.models import init_catalog_search_models, init_catalog_audit_log
        from catalog import api

        init_catalog_search_models()
        init_catalog_audit_log()
