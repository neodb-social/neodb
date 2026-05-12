import logging

import django_rq
from loguru import logger
from rq import cron as rq_cron

from common.management.base import SiteCommand
from common.models import JobManager
from common.models.cron import CRON_QUEUE

CRON_CONFIG_MODULE = "boofilsic.cron_config"


class Command(SiteCommand):
    help = "Manage cron jobs via rq.cron"

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            action="store_true",
            help="Run the rq.cron scheduler in the foreground.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List registered jobs and any active schedulers.",
        )
        parser.add_argument(
            "--run-once",
            action="append",
            help="Execute the named job synchronously in this process.",
        )

    def handle(self, *args, **options):
        if options["run_once"]:
            for job_id in options["run_once"]:
                JobManager.get(job_id)().run()
        if options["list"]:
            self._list()
        if options["start"]:
            self._start()

    def _list(self):
        # Ensure all jobs are imported into the registry.
        __import__(CRON_CONFIG_MODULE, fromlist=["*"])
        names = sorted(j.__name__ for j in JobManager.registry)
        logger.info(f"{len(names)} registered jobs: {' '.join(names)}")
        try:
            schedulers = JobManager.get_active_schedulers()
        except Exception as e:
            logger.warning(f"Unable to query active schedulers: {e}")
            return
        logger.info(f"{len(schedulers)} active cron scheduler(s):")
        for s in schedulers:
            for job in s.get_jobs():
                logger.info(
                    f"  {s.name}  {job.func_name}  args={job.args}  "
                    f"interval={job.interval}  cron={job.cron}  "
                    f"next={job.next_enqueue_time}"
                )

    def _start(self):
        connection = django_rq.get_connection(CRON_QUEUE)
        scheduler = rq_cron.CronScheduler(
            connection=connection,
            logging_level=logging.INFO,
        )
        scheduler.load_config_from_file(CRON_CONFIG_MODULE)
        scheduler.start()
