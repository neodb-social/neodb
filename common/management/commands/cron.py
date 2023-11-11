from django.core.management.base import BaseCommand
from loguru import logger

from catalog.jobs import *  # noqa
from common.models import JobManager
from mastodon.jobs import *  # noqa
from users.jobs import *  # noqa


class Command(BaseCommand):
    help = "Schedule timed jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cancel",
            action="store_true",
        )
        parser.add_argument(
            "--schedule",
            action="store_true",
        )
        parser.add_argument(
            "--list",
            action="store_true",
        )
        parser.add_argument(
            "--runonce",
            action="append",
        )

    def handle(self, *args, **options):
        if options["cancel"]:
            JobManager.cancel()
        if options["schedule"]:
            JobManager.cancel()  # cancel previously scheduled jobs if any
            JobManager.schedule()
        if options["runonce"]:
            for job_id in options["runonce"]:
                run = JobManager.run(job_id)
                if not run:
                    logger.error(f"Job not found: {job_id}")
        if options["list"]:
            all_jobs = [j.__name__ for j in JobManager.registry]
            logger.info(f"{len(all_jobs)} available jobs: {' '.join(all_jobs)}")
            jobs = JobManager.get_scheduled_job_ids()
            logger.info(f"{len(jobs)} scheduled jobs: {' '.join(jobs)}")
