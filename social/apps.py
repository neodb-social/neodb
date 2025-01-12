from django.apps import AppConfig


class SocialConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "social"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        from catalog import models as catalog_models  # noqa
        from catalog import sites as catalog_sites  # noqa
        from journal import models as journal_models  # noqa
        from social import models as social_models  # noqa
