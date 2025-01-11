from django.apps import AppConfig


class SocialConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "social"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        pass
