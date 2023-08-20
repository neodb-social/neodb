from django.core.management.base import BaseCommand

from catalog.search.typesense import Indexer


class Command(BaseCommand):
    help = "Post-Migration Setup"

    def handle(self, *args, **options):
        # Update site name if changed

        # Create/update admin user if configured in env

        # Create basic emoji if not exists

        # Create search index if not exists
        Indexer.init()

        # Register cron jobs if not yet
