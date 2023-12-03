from django.apps import AppConfig


class MastodonConfig(AppConfig):
    name = "mastodon"

    def ready(self):
        # register cron jobs
        from mastodon.jobs import MastodonSiteCheck  # isort:skip
