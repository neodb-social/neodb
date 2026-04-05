from django.apps import AppConfig
from django.core.checks import Tags, register
from django.db.models.signals import post_migrate


class CommonConfig(AppConfig):
    name = "common"

    def ready(self):
        post_migrate.connect(self.setup, sender=self)

    def setup(self, **kwargs):
        from .setup import Setup

        if kwargs.get("using", "") == "default":
            # only run setup on the default database, not on takahe
            Setup().run()


@register(Tags.admin, deploy=True)
def setup_check(app_configs, **kwargs):
    from .setup import Setup

    return Setup().check()
