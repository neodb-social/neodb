from django.conf import settings
from django.core.checks import Error, Warning
from loguru import logger

from catalog.search import CatalogIndex
from common.models import JobManager, SiteConfig
from journal.search import JournalIndex
from takahe.models import Config as TakaheConfig
from takahe.models import Domain as TakaheDomain
from takahe.models import Identity as TakaheIdentity
from takahe.models import Relay as TakaheRelay
from takahe.utils import Takahe


class Setup:
    """
    Post-Migration Setup
    """

    def create_site(self, domain, service_domain):
        TakaheDomain.objects.update_or_create(
            domain=domain,
            defaults={
                "local": True,
                "service_domain": service_domain,
                "notes": "NeoDB",
                "nodeinfo": {},
                "state": "updated",
            },
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
                    "Local identities are found for other domains, there might be a configuration issue."
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

    def sync_relay(self):
        relay = TakaheRelay.objects.filter(
            state__in=["new", "subscribing", "subscribed"],
            inbox_uri=settings.DEFAULT_RELAY_SERVER,
        ).first()
        if SiteConfig.system.disable_default_relay:
            if relay:
                logger.info("Default relay is disabled, unsubscribing...")
                Takahe.update_state(relay, "unsubscribing")
            else:
                logger.info("Default relay is disabled.")
        else:
            if relay:
                logger.debug(f"Default relay is enabled, state: {relay.state}")
            else:
                logger.info("Default relay is enabled, subscribing...")
                TakaheRelay.objects.update_or_create(
                    inbox_uri=settings.DEFAULT_RELAY_SERVER,
                    defaults={"state": "new"},
                )

    def run(self):
        logger.info("Running post-migration setup...")
        SiteConfig.ensure_loaded()

        # Update site name if changed (queries takahe DB which may not be ready)
        from django.db import connections

        try:
            with connections["takahe"].cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'users_domain'"
                )
                if cursor.fetchone():
                    self.sync_site_config()
                else:
                    logger.warning(
                        "sync_site_config skipped: takahe DB not migrated yet"
                    )
        except Exception as e:
            logger.warning(f"sync_site_config skipped: {e}")

        # Create basic emoji if not exists

        # Create search index if not exists
        CatalogIndex.instance().initialize_collection()
        JournalIndex.instance().initialize_collection()

        if settings.TESTING:
            # Only do necessary initialization when testing
            logger.info("Finished post-migration setup, skipped some for testing.")
            return

        # Register cron jobs if not yet
        if (
            SiteConfig.system.disable_cron_jobs
            and "*" in SiteConfig.system.disable_cron_jobs
        ):
            logger.info("Cron jobs are disabled.")
            JobManager.cancel_all()
        else:
            JobManager.reschedule_all()

        # Subscribe to default relay if enabled
        self.sync_relay()

        logger.info("Finished post-migration setup.")

    def check(self):
        from redis import Redis

        errors = []
        # check env
        domain = settings.SITE_INFO.get("site_domain")
        if not domain:
            errors.append(
                Error(
                    "SITE DOMAIN is not specified",
                    hint="Check NEODB_SITE_DOMAIN in .env",
                    id="neodb.E001",
                )
            )
        # check redis
        try:
            redis = Redis.from_url(settings.REDIS_URL)
            if not redis:
                raise Exception("Redis unavailable")
            redis.ping()
        except Exception as e:
            errors.append(
                Error(
                    f"Error while connecting to redis: {e}",
                    hint="Check NEODB_REDIS_URL/TAKAHE_CACHES_DEFAULT in .env",
                    id="neodb.E002",
                )
            )
        # check indexer
        r = CatalogIndex.instance().initialize_collection()
        if not r:
            errors.append(
                Error(
                    "Catalog index is not available",
                    hint="Check NEODB_SEARCH_URL in .env, and run neodb-manage catalog idx-init",
                    id="neodb.E003",
                )
            )
        r = JournalIndex.instance().initialize_collection()
        if not r:
            errors.append(
                Error(
                    "Journal index is not available",
                    hint="Check NEODB_SEARCH_URL in .env, and run neodb-manage journal idx-init",
                    id="neodb.E003",
                )
            )

        # check takahe
        try:
            if not TakaheDomain.objects.filter(domain=domain).exists():
                errors.append(
                    Warning(
                        f"Domain {domain} not found in takahe database",
                        hint="Run migration once to create the domain",
                        id="neodb.W001",
                    )
                )
        except Exception as e:
            errors.append(
                Error(
                    f"Error while querying takahe database: {e}",
                    hint='Check TAKAHE_DB_URL/TAKAHE_DATABASE_SERVER in .env, and run "takahe-manage migration"',
                    id="neodb.E004",
                )
            )
        return errors
