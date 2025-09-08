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
        except Exception:
            pass

    @classmethod
    def schedule(cls, now=False):
        job_id = cls.__name__
        i = timedelta(seconds=0) if now else cls.interval
        if cls.interval <= timedelta(0) or job_id in settings.DISABLE_CRON_JOBS:
            logger.info(f"Skip disabled job {job_id}")
            return
        logger.info(f"Scheduling job {job_id} in {i}")
        if now:
            django_rq.get_queue("cron").enqueue(
                cls._run,
                job_id=job_id,
                result_ttl=-1,
                failure_ttl=-1,
                job_timeout=cls.interval.seconds - 5,
            )
        else:
            django_rq.get_queue("cron").enqueue_in(
                cls.interval,
                cls._run,
                job_id=job_id,
                result_ttl=-1,
                failure_ttl=-1,
                job_timeout=cls.interval.seconds - 5,
            )

    @classmethod
    def reschedule(cls, now: bool = False):
        cls.cancel()
        cls.schedule(now=now)

    @classmethod
    def _run(cls):
        cls.schedule()  # schedule next run
        cls().run()

    def run(self):
        pass


class JobManager:
    registry: set[type[BaseJob]] = set()

    @classmethod
    def register(cls, target):
        cls.registry.add(target)
        return target

    @classmethod
    def get(cls, job_id) -> type[BaseJob]:
        for j in cls.registry:
            if j.__name__ == job_id:
                return j
        raise KeyError(f"Job not found: {job_id}")

    @classmethod
    def get_scheduled_job_ids(cls):
        registry = ScheduledJobRegistry(queue=django_rq.get_queue("cron"))
        return registry.get_job_ids()

    @classmethod
    def schedule_all(cls):
        for j in cls.registry:
            j.schedule()

    @classmethod
    def cancel_all(cls):
        for j in cls.registry:
            j.cancel()

    @classmethod
    def reschedule_all(cls):
        cls.cancel_all()
        cls.schedule_all()
