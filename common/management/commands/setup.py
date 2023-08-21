from django.conf import settings
from django.core.management.base import BaseCommand
from loguru import logger

from catalog.search.typesense import Indexer
from takahe.models import Config as TakaheConfig
from takahe.models import Domain as TakaheDomain
from takahe.models import Identity as TakaheIdentity
from takahe.models import User as TakaheUser
from users.models import User


class Command(BaseCommand):
    help = "Post-Migration Setup"

    def sync_site_config(self):
        domain = settings.SITE_INFO["site_domain"]
        icon = settings.SITE_INFO["site_logo"]
        name = settings.SITE_INFO["site_name"]
        service_domain = settings.SITE_INFO.get("site_service_domain")
        TakaheConfig.objects.update_or_create(
            key="site_name",
            user=None,
            identity=None,
            domain=None,
            defaults={"json": name},
        )
        TakaheConfig.objects.update_or_create(
            key="site_name",
            user=None,
            identity=None,
            domain_id=domain,
            defaults={"json": name},
        )
        TakaheConfig.objects.update_or_create(
            key="site_icon",
            user=None,
            identity=None,
            domain_id=None,
            defaults={"json": icon},
        )
        TakaheConfig.objects.update_or_create(
            key="site_icon",
            user=None,
            identity=None,
            domain_id=domain,
            defaults={"json": icon},
        )

    def sync_admin_user(self):
        users = User.objects.filter(username__in=settings.SETUP_ADMIN_USERNAMES)
        for user in users:
            if user.is_superuser:
                logger.debug(f"User {user.username} is already admin")
            else:
                user.is_superuser = True
                user.save(update_fields=["is_superuser"])
                TakaheUser.objects.filter(email="@" + user.username).update(admin=True)
                logger.info(f"Updated user {user.username} as admin")

    def handle(self, *args, **options):
        # Update site name if changed
        self.sync_site_config()

        # Create/update admin user if configured in env
        self.sync_admin_user()

        # Create basic emoji if not exists

        # Create search index if not exists
        Indexer.init()

        # Register cron jobs if not yet
