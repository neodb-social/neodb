from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CommonConfig(AppConfig):
    name = "common"

    def ready(self):
        post_migrate.connect(self.setup, sender=self)

    def setup(self, **kwargs):
        from .setup import Setup

        Setup().run()
