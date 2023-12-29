from datetime import timedelta

import django_rq
from loguru import logger
from rq.job import Job
from rq.registry import ScheduledJobRegistry

from boofilsic import settings


class BaseJob:
    interval = timedelta(0)  # 0 = disabled, don't set it less than 1 minute

    @classmethod
    def cancel(cls):
        job_id = cls.__name__
        try:
            job = Job.fetch(id=job_id, connection=django_rq.get_connection("cron"))
            if job.get_status() in ["queued", "scheduled"]:
                logger.info(f"Cancel queued job: {job_id}")
                job.cancel()
            registry = ScheduledJobRegistry(queue=django_rq.get_queue("cron"))
            registry.remove(job)
        except:
            pass

    @classmethod
    def schedule(cls):
        job_id = cls.__name__
        if cls.interval <= timedelta(0) or job_id in settings.DISABLE_CRON_JOBS:
            logger.info(f"Skip scheduling job: {job_id}")
            return
        logger.info(f"Scheduling job: {job_id} in {cls.interval}")
        django_rq.get_queue("cron").enqueue_in(
            cls.interval,
            cls._run,
            job_id=job_id,
            result_ttl=-1,
            failure_ttl=-1,
            job_timeout=cls.interval.seconds - 5,
        )

    @classmethod
    def _run(cls):
        cls.schedule()  # schedule next run
        cls().run()

    def run(self):
        pass


class JobManager:
    registry = set()

    @classmethod
    def register(cls, target):
        cls.registry.add(target)
        return target

    @classmethod
    def schedule_all(cls):
        for j in cls.registry:
            j.schedule()

    @classmethod
    def cancel_all(cls):
        for j in cls.registry:
            j.cancel()

    @classmethod
    def run(cls, job_id):
        for j in cls.registry:
            if j.__name__ == job_id:
                logger.info(f"Run job: {job_id}")
                j().run()
                return True
        return False

    @classmethod
    def get_scheduled_job_ids(cls):
        registry = ScheduledJobRegistry(queue=django_rq.get_queue("cron"))
        return registry.get_job_ids()

    @classmethod
    def reschedule_all(cls):
        cls.cancel_all()
        cls.schedule_all()
