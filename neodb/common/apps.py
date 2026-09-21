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

    Every media key would sit in the url space of the site itself.
    """
    if not settings.MEDIA_BACKEND.startswith("s3"):
        return []
    # an empty MEDIA_URL leaves this unset and urls address the s3 endpoint;
    # django reads it back as "/", which looks like the site root below
    if not getattr(settings, "AWS_S3_CUSTOM_DOMAIN", ""):
        return []
    # as renderers.py and jobs/migrations.py read it
    site_domains = getattr(settings, "SITE_DOMAINS", [settings.SITE_DOMAIN])
    if not media_url_at_site_root(settings.MEDIA_URL, site_domains):
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
    # not a deploy check: a plain ``neodb-manage check`` should say so
    return media_url_errors()
