from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from journal.models import Comment, Review, ShelfMember
from mastodon.api import detect_server_info
from mastodon.models import MastodonApplication
from takahe.models import Domain, Identity, Post


@JobManager.register
class TakaheStats(BaseJob):
    interval = timedelta(hours=6)
    max_unreachable_days = 31

    def active_users(self, d: int) -> int:
        return (
            ShelfMember.objects.filter(
                created_time__gte=timezone.now() - timedelta(days=d), local=True
            )
            .values("owner_id")
            .distinct()
            .count()
        )

    def run(self):
        logger.info("Updating Tahake stats.")
        # for /api/v1/instance
        stats = {
            "user_count": Identity.objects.filter(
                local=True, deleted__isnull=True
            ).count(),
            "status_count": Post.objects.filter(local=True)
            .exclude(state__in=["deleted", "deleted_fanned_out"])
            .count(),
            "domain_count": Domain.objects.count(),
        }
        cache.set("instance_info_stats", stats)
        logger.debug(f"/api/v1/instance {stats}")
        # for /api/v2/instance
        usage = {
            "users": {
                "active_month": self.active_users(30),
            }
        }
        cache.set("instance_info_usage", usage)
        logger.debug(f"/api/v2/instance {usage}")
        # for NodeInfo
        nodeinfo_usage = {
            "users": {
                "total": stats["user_count"],
                "activeMonth": usage["users"]["active_month"],
                "activeHalfyear": self.active_users(180),
            },
            "localPosts": ShelfMember.objects.filter(local=True).count(),
            "localComments": Comment.objects.filter(local=True).count()
            + Review.objects.filter(local=True).count(),
        }
        cache.set("nodeinfo_usage", nodeinfo_usage)
        logger.debug(f"/nodeinfo/2.0/ {nodeinfo_usage}")
        logger.info("Tahake stats updated.")
