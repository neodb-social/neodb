from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.db.models.signals import post_migrate

from .media_url import media_url_at_site_root


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


def media_url_errors() -> list[CheckMessage]:
    """MEDIA_URL must not serve a remote backend from the site root.

    Media on one of our own domains is addressed by path, and a root path
    leaves every media key in the url space of the site itself, where the
    application's own paths already live.
    """
    if not settings.MEDIA_BACKEND.startswith("s3"):
        return []
    if not media_url_at_site_root(settings.MEDIA_URL, settings.SITE_DOMAINS):
        return []
    return [
        Error(
            f"MEDIA_URL {settings.MEDIA_URL!r} serves media from the root of "
            "the site domain",
            hint="Give MEDIA_URL a path of its own, e.g. "
            f"https://{settings.SITE_DOMAIN}/m/, or point it at a separate "
            "media host. Check MEDIA_URL in .env",
            id="neodb.E005",
        )
    ]


@register(Tags.files)
def media_url_check(app_configs, **kwargs):
    # not a deploy check: a misconfigured instance should hear about this
    # from a plain ``neodb-manage check``
    return media_url_errors()
