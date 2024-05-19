from django.apps import AppConfig


class TakaheConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "takahe"

    def ready(self):
        # register cron jobs
        from .jobs import TakaheStats  # isort:skip
