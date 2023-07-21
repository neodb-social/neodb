from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, F
from loguru import logger
from tqdm import tqdm

from catalog.common import *
from catalog.common.models import *
from catalog.models import *
from journal.models import Tag, update_journal_for_merged_item
from takahe.utils import *
from users.models import User as NeoUser


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
        )

    def sync(self):
        logger.info(f"Syncing domain...")
        Takahe.get_domain()
        logger.info(f"Syncing users...")
        for u in tqdm(NeoUser.objects.filter(is_active=True, username__isnull=False)):
            Takahe.init_identity_for_local_user(u)
            # Takahe.update_user_following(u)
            # Takahe.update_user_muting(u)
            # Takahe.update_user_rejecting(u)

    def handle(self, *args, **options):
        self.verbose = options["verbose"]

        if options["sync"]:
            self.sync()

        self.stdout.write(self.style.SUCCESS(f"Done."))
