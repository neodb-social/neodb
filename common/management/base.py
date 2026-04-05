from django.core.management.base import BaseCommand, CommandError

from common.models.site_config import SiteConfig

__all__ = ["SiteCommand", "CommandError"]


class SiteCommand(BaseCommand):
    """Base command that ensures SiteConfig is loaded before execution."""

    def execute(self, *args, **options):
        SiteConfig.ensure_loaded()
        return super().execute(*args, **options)
