from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "users"

    def ready(self):
        # register cron jobs
        pass  # isort:skip
