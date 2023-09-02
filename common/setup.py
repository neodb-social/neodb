from django.conf import settings
from loguru import logger

from catalog.search.typesense import Indexer
from takahe.models import Config as TakaheConfig
from takahe.models import Domain as TakaheDomain
from takahe.models import Identity as TakaheIdentity
from takahe.models import User as TakaheUser
from users.models import User


class Setup:
    """
    Post-Migration Setup
    """

    def create_site(self, domain, service_domain):
        TakaheDomain.objects.create(
            domain=domain,
            local=True,
            service_domain=service_domain,
            notes="NeoDB",
            nodeinfo={},
        )
        TakaheConfig.objects.update_or_create(
            key="public_timeline",
            user=None,
            identity=None,
            domain=None,
            defaults={"json": False},
        )

    def sync_site_config(self):
        domain = settings.SITE_INFO["site_domain"]
        if not domain:
            raise ValueError("Panic: site_domain is not set!")
        icon = settings.SITE_INFO["site_logo"]
        name = settings.SITE_INFO["site_name"]
        service_domain = settings.SITE_INFO.get("site_service_domain")

        if not TakaheDomain.objects.filter(domain=domain).exists():
            logger.info(f"Domain {domain} not found, creating...")
            self.create_site(domain, service_domain)
            if (
                TakaheIdentity.objects.filter(local=True)
                .exclude(domain_id__isnull=True)
                .exists()
            ):
                logger.warning(
                    f"Local identities are found for other domains, there might be a configuration issue."
                )

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
                TakaheUser.objects.filter(email=f"@{user.username}").update(admin=True)
                logger.info(f"Updated user {user.username} as admin")

    def run(self):
        logger.info("Running post-migration setup...")
        # Update site name if changed
        self.sync_site_config()

        # Create/update admin user if configured in env
        self.sync_admin_user()

        # Create basic emoji if not exists

        # Create search index if not exists
        Indexer.init()

        # Register cron jobs if not yet

        logger.info("Finished post-migration setup.")
