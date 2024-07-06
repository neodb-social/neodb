from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from mastodon.models import MastodonApplication, detect_server_info, verify_client


@JobManager.register
class MastodonSiteCheck(BaseJob):
    interval = timedelta(days=1)
    max_unreachable_days = 31

    def run(self):
        logger.info("Mastodon Site Check start.")
        count_checked = 0
        count_unreachable = 0
        count_disabled = 0
        q = Q(last_reachable_date__lte=timezone.now() - timedelta(days=1)) | Q(
            last_reachable_date__isnull=True
        )
        for site in MastodonApplication.objects.exclude(disabled=True).filter(q):
            domain = None
            count_checked += 1
            try:
                api_domain = site.api_domain or site.domain_name
                domain, api_domain, v = detect_server_info(api_domain)
                site.last_reachable_date = timezone.now()
                site.detect_configurations()
            except Exception as e:
                logger.error(
                    f"Failed to detect server info for {site.domain_name}/{site.api_domain}",
                    extra={"exception": e},
                )
                count_unreachable += 1
                if site.last_reachable_date is None:
                    site.last_reachable_date = timezone.now() - timedelta(days=1)
                if timezone.now() > site.last_reachable_date + timedelta(
                    days=self.max_unreachable_days
                ):
                    site.disabled = True
                    count_disabled += 1
            finally:
                site.save(
                    update_fields=[
                        "star_mode",
                        "max_status_len",
                        "last_reachable_date",
                        "disabled",
                    ]
                )
            # try:
            #     if not verify_client(site):
            #         logger.error(
            #             f"Unable to verify client app for {site.api_domain}, consider deleting it."
            #         )
            #         # site.delete()
            # except Exception as e:
            #     logger.error(
            #         f"Failed to verify client app for {site.api_domain}",
            #         extra={"exception": e},
            #     )
        logger.info(
            f"Mastodon Site Check finished, {count_checked} checked, {count_unreachable} unreachable, {count_disabled} disabled."
        )
