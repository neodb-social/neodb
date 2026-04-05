from typing import Any

from rq.job import Job


class SiteConfigJob(Job):
    """Custom RQ Job class that reloads SiteConfig before each job execution."""

    def perform(self) -> Any:
        from common.models.site_config import SiteConfig

        SiteConfig.reload()
        return super().perform()
