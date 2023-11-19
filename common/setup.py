from django.conf import settings
from loguru import logger

from catalog.search.models import Indexer
from common.models import JobManager
from takahe.models import Config as TakaheConfig
from takahe.models import Domain as TakaheDomain
from takahe.models import Follow as TakaheFollow
from takahe.models import Identity as TakaheIdentity
from takahe.models import User as TakaheUser
from takahe.utils import Takahe
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

    def sync_relay(self):
        relay_follow = TakaheFollow.objects.filter(
            source__username="__relay__",
            source__local=True,
            target__actor_uri=settings.DEFAULT_RELAY_SERVER,
        ).first()
        if settings.DISABLE_DEFAULT_RELAY:
            if relay_follow:
                logger.info("Default relay is disabled, unsubscribing...")
                Takahe.create_internal_message(
                    {
                        "type": "UnfollowRelay",
                        "actor_uri": settings.DEFAULT_RELAY_SERVER,
                    }
                )
            else:
                logger.debug(f"Default relay is disabled.")
        else:
            if relay_follow:
                logger.debug(
                    f"Default relay is enabled and subscribed, state: {relay_follow.state}"
                )
            else:
                logger.info("Default relay is enabled, subscribing...")
                relay_actor = TakaheIdentity.objects.filter(
                    username="__relay__",
                    local=True,
                ).first()
                if not relay_actor:
                    logger.warning(
                        f"Default relay is enabled but relay actor does not exist."
                    )
                    return
                Takahe.create_internal_message(
                    {
                        "type": "AddFollow",
                        "source": relay_actor.pk,
                        "target_actor": settings.DEFAULT_RELAY_SERVER,
                        "boosts": False,
                    }
                )

    def run(self):
        logger.info("Running post-migration setup...")
        # Update site name if changed
        self.sync_site_config()

        # Create/update admin user if configured in env
        self.sync_admin_user()

        # Subscribe to default relay if enabled
        self.sync_relay()

        # Create basic emoji if not exists

        # Create search index if not exists
        Indexer.init()

        # Register cron jobs if not yet
        JobManager.schedule_all()

        logger.info("Finished post-migration setup.")
