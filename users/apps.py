from django.apps import AppConfig
from django.conf import settings
import django_rq
from datetime import datetime


class UsersConfig(AppConfig):
    name = "users"

    def ready(self):
        from .tasks import refresh_all_users_mastodon_data_task

        scheduler = django_rq.get_scheduler('mastodon')

        for job in scheduler.get_jobs():
            job.delete()

        # run every hour
        scheduler.schedule(
            datetime.utcnow(),
            refresh_all_users_mastodon_data_task,
            interval=settings.CRON_INTERVAL_REFRESH_MASTODON,
            result_ttl=settings.CRON_TTL_REFRESH_MASTODON,
        )
