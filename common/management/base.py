from django.core.management.base import BaseCommand


class SiteCommand(BaseCommand):
    """Base command that ensures SiteConfig is loaded before execution."""

    def execute(self, *args, **options):
        from common.models.site_config import SiteConfig

        SiteConfig.ensure_loaded()
        return super().execute(*args, **options)
