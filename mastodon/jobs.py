from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from mastodon.api import detect_server_info
from mastodon.models import MastodonApplication


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
            except:
                logger.warning(
                    f"Failed to detect server info for {site.domain_name}/{site.api_domain}"
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
                site.save(update_fields=["last_reachable_date", "disabled"])
        logger.info(
            f"Mastodon Site Check finished, {count_checked} checked, {count_unreachable} unreachable, {count_disabled} disabled."
        )
