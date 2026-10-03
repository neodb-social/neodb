import logging
import random
from datetime import timedelta

import django_rq
from django.utils import timezone
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.registry import ScheduledJobRegistry

from common.models.site_config import SiteConfig

logger = logging.getLogger(__name__)

# A freshly scheduled long-interval job first runs within this window instead
# of a full interval later, so a daily or weekly job still runs on a cluster
# that seldom stays up for a whole interval.
FIRST_RUN_WINDOW = (timedelta(hours=6), timedelta(hours=12))


class BaseJob:
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
    def get_interval(cls) -> timedelta:
        """Return job interval. Override to read from SiteConfig."""
        return timedelta(0)

    @classmethod
    def get_enabled_interval(cls) -> timedelta | None:
        interval = cls.get_interval()
        disabled = (
            getattr(SiteConfig, "system", None)
            and SiteConfig.system.disable_cron_jobs
            or []
        )
        if interval <= timedelta(0) or cls.__name__ in disabled:
            return None
        return interval

    @classmethod
    def _enqueue(cls, interval: timedelta, delay: timedelta) -> None:
        job_id = cls.__name__
        logger.info(f"Scheduling job {job_id} in {delay}")
        queue = django_rq.get_queue("cron")
        options = {
            "job_id": job_id,
            "result_ttl": -1,
            "failure_ttl": -1,
            "job_timeout": int(interval.total_seconds()) - 5,
        }
        if delay <= timedelta(0):
            queue.enqueue(cls._run, **options)
        else:
            queue.enqueue_in(delay, cls._run, **options)

    @classmethod
    def schedule(cls, now: bool = False) -> None:
        interval = cls.get_enabled_interval()
        if interval is None:
            logger.info(f"Skip disabled job {cls.__name__}")
            return
        cls._enqueue(interval, timedelta(0) if now else interval)

    @classmethod
    def get_first_run_delay(cls, interval: timedelta) -> timedelta:
        low, high = FIRST_RUN_WINDOW
        if interval <= low:
            return interval
        high = min(high, interval)
        return timedelta(
            seconds=random.uniform(low.total_seconds(), high.total_seconds())
        )

    @classmethod
    def ensure_scheduled(cls) -> None:
        """
        Schedule the job at startup without losing the pending run.

        _run() schedules the next run before it starts, so the pending entry
        already holds last start + interval, and redis keeps it across a
        restart. Keep it unless it is more than one interval away (the
        interval got shorter). Keep an overdue one too: the rq scheduler
        enqueues it as soon as a worker starts. Without a usable entry,
        schedule the first run with get_first_run_delay().
        """
        job_id = cls.__name__
        interval = cls.get_enabled_interval()
        if interval is None:
            logger.info(f"Skip disabled job {job_id}")
            cls.cancel()
            return
        queue = django_rq.get_queue("cron")
        registry = ScheduledJobRegistry(queue=queue)
        try:
            scheduled_at = registry.get_scheduled_time(job_id)
        except NoSuchJobError:
            scheduled_at = None
        if scheduled_at is not None and Job.exists(job_id, queue.connection):
            if scheduled_at <= timezone.now() + interval:
                logger.info(f"Keep job {job_id} scheduled at {scheduled_at}")
                return
        elif scheduled_at is None and job_id in queue.get_job_ids():
            logger.info(f"Keep job {job_id} queued")
            return
        cls.cancel()
        registry.remove(job_id)
        cls._enqueue(interval, cls.get_first_run_delay(interval))

    @classmethod
    def reschedule(cls, now: bool = False):
        cls.cancel()
        cls.schedule(now=now)

    @classmethod
    def _run(cls):
        # SiteConfig is reloaded automatically by SiteConfigJob.perform()
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
    def ensure_all(cls):
        for j in cls.registry:
            j.ensure_scheduled()

    @classmethod
    def cancel_all(cls):
        for j in cls.registry:
            j.cancel()

    @classmethod
    def reschedule_all(cls):
        cls.cancel_all()
        cls.schedule_all()
