from datetime import timedelta

import django_rq
from loguru import logger
from rq import cron as rq_cron

from common.models.site_config import SiteConfig

CRON_QUEUE = "cron"


def run_job(job_id: str) -> None:
    """Module-level entry point used by rq.cron / RQ workers.

    Looks up the BaseJob subclass in the registry and runs it. Defined at the
    module level (not as a classmethod) so RQ can resolve it by import path
    on the worker side.
    """
    try:
        job_cls = JobManager.get(job_id)
    except KeyError:
        # Workers spawned without going through AppConfig.ready() (or where the
        # app set is reduced) won't have the registry populated. The config
        # module re-imports every jobs module.
        __import__("boofilsic.cron_config", fromlist=["*"])
        job_cls = JobManager.get(job_id)
    job_cls().run()


class BaseJob:
    """Base class for periodic jobs scheduled via rq.cron.

    Subclasses override ``get_interval()`` (returning a ``timedelta``) and
    ``run()``. The scheduler lifecycle is owned by an external
    ``rq.cron.CronScheduler`` process, not by the job itself.
    """

    queue_name: str = CRON_QUEUE

    @classmethod
    def get_interval(cls) -> timedelta:
        """Return job interval. Override to read from SiteConfig.

        A non-positive interval disables the job.
        """
        return timedelta(0)

    @classmethod
    def get_cron(cls) -> str | None:
        """Return a cron expression. Overrides ``get_interval`` when set."""
        return None

    @classmethod
    def get_job_timeout(cls) -> int | None:
        """Default timeout slightly shorter than the interval to avoid overlap."""
        seconds = int(cls.get_interval().total_seconds())
        return seconds - 5 if seconds > 5 else None

    def run(self):
        pass


class JobManager:
    registry: set[type[BaseJob]] = set()

    @classmethod
    def register(cls, target: type[BaseJob]) -> type[BaseJob]:
        cls.registry.add(target)
        return target

    @classmethod
    def get(cls, job_id: str) -> type[BaseJob]:
        for j in cls.registry:
            if j.__name__ == job_id:
                return j
        raise KeyError(f"Job not found: {job_id}")

    @classmethod
    def _disabled_set(cls) -> tuple[set[str], bool]:
        disabled = (
            getattr(SiteConfig, "system", None)
            and SiteConfig.system.disable_cron_jobs
            or []
        )
        return set(disabled), "*" in disabled

    @classmethod
    def register_with_rq_cron(cls) -> int:
        """Register every job in the registry with the rq.cron global registry.

        Called from a cron config module loaded by ``CronScheduler``.
        Returns the number of jobs registered.
        """
        disabled, wildcard = cls._disabled_set()
        count = 0
        for j in sorted(cls.registry, key=lambda c: c.__name__):
            job_id = j.__name__
            if wildcard or job_id in disabled:
                logger.info(f"Skip disabled cron job: {job_id}")
                continue
            cron_expr = j.get_cron()
            interval_seconds = int(j.get_interval().total_seconds())
            if not cron_expr and interval_seconds <= 0:
                logger.info(f"Skip cron job with no schedule: {job_id}")
                continue
            options: dict = {
                "func": run_job,
                "queue_name": j.queue_name,
                "args": (job_id,),
                # Each tick creates a fresh job UUID under rq.cron (unlike the
                # old fixed-job_id scheme that overwrote on re-enqueue), so
                # finite TTLs are required to avoid unbounded Redis growth.
                "result_ttl": 3600,
                "failure_ttl": 604800,
            }
            timeout = j.get_job_timeout()
            if timeout is not None:
                options["job_timeout"] = timeout
            if cron_expr:
                options["cron"] = cron_expr
                logger.info(f"Registering cron job {job_id} with cron '{cron_expr}'")
            else:
                options["interval"] = interval_seconds
                logger.info(f"Registering cron job {job_id} every {interval_seconds}s")
            rq_cron.register(**options)
            count += 1
        return count

    @classmethod
    def get_active_schedulers(cls):
        """Return live ``CronScheduler`` snapshots from Redis (one per process)."""
        return rq_cron.CronScheduler.all(
            connection=django_rq.get_connection(CRON_QUEUE)
        )
