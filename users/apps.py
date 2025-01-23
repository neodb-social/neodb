from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "users"

    def ready(self):
        from . import apis  # noqa

        # register cron jobs
        from users.jobs import MastodonUserSync  # noqa
