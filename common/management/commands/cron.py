import random
from time import sleep

from django.core.management.base import BaseCommand
from loguru import logger

from common.models import BaseJob, JobManager

# @JobManager.register
# class DummyJob(BaseJob):
#     interval = timedelta(seconds=10)

#     def run(self):
#         logger.info("Dummy job started")
#         if random.choice([0, 1]) == 0:
#             raise Exception("Dummy job randomly failed")
#         sleep(3)
#         logger.info("Dummy job stopped")


class Command(BaseCommand):
    help = "Schedule timed jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cancel-all",
            action="store_true",
        )
        parser.add_argument(
            "--reschedule-all",
            action="store_true",
        )
        parser.add_argument(
            "--list",
            action="store_true",
        )
        parser.add_argument(
            "--run-once",
            action="append",
        )
        parser.add_argument(
            "--reschedule-now",
            action="append",
        )

    def handle(self, *args, **options):
        if options["cancel_all"]:
            JobManager.cancel_all()
        if options["reschedule_all"]:
            JobManager.reschedule_all()
        if options["reschedule_now"]:
            for job_id in options["reschedule_now"]:
                JobManager.get(job_id).reschedule(now=True)
        if options["run_once"]:
            for job_id in options["run_once"]:
                JobManager.get(job_id)().run()
        if options["list"]:
            all_jobs = [j.__name__ for j in JobManager.registry]
            logger.info(f"{len(all_jobs)} available jobs: {' '.join(all_jobs)}")
            jobs = JobManager.get_scheduled_job_ids()
            logger.info(f"{len(jobs)} scheduled jobs: {' '.join(jobs)}")
