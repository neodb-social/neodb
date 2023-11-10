from datetime import timedelta

from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from mastodon.api import detect_server_info
from mastodon.models import MastodonApplication


@JobManager.register
class MastodonSiteCheck(BaseJob):
    max_unreachable_days = 31

    def run(self):
        logger.info("Mastodon Site Check start.")
        count_checked = 0
        count_unreachable = 0
        count_disabled = 0
        for site in MastodonApplication.objects.exclude(disabled=True):
            domain = None
            count_checked += 1
            try:
                domain, api_domain, v = detect_server_info(site.domain_name)
                site.last_reachable_date = timezone.now()
            except:
                logger.warning(f"Failed to detect server info for {site.domain_name}")
                count_unreachable += 1
                if site.last_reachable_date is None:
                    site.last_reachable_date = timezone.now() - timedelta(days=1)
                if timezone.now() > site.last_reachable_date + timedelta(
                    days=self.max_unreachable_days
                ):
                    site.disabled = True
                    count_disabled += 1
            finally:
                site.save(update_fields=["last_reachable_date", "disabled"])
        logger.info(
            f"Mastodon Site Check finished, {count_checked} checked, {count_unreachable} unreachable, {count_disabled} disabled."
        )
