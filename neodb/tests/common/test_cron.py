from datetime import timedelta

import django_rq
import pytest
from django.utils import timezone
from rq import Queue
from rq.job import Job
from rq.registry import ScheduledJobRegistry

from common.models import cron
from common.models.cron import BaseJob
from common.models.site_config import SiteConfig
from common.rq import SiteJob


class CronTestJob(BaseJob):
    interval = timedelta(days=1)

    @classmethod
    def get_interval(cls) -> timedelta:
        return cls.interval


@pytest.fixture
def queue(monkeypatch):
    # a private queue name keeps the dev cluster worker away from the test job
    q = Queue(
        "neodb-test-cron",
        connection=django_rq.get_connection("cron"),
        job_class=SiteJob,
    )
    monkeypatch.setattr(cron.django_rq, "get_queue", lambda name: q)
    monkeypatch.setattr(CronTestJob, "interval", timedelta(days=1))
    CronTestJob.cancel()
    q.empty()
    yield q
    CronTestJob.cancel()
    q.empty()
    q.connection.delete(Job.key_for("CronTestJob"))
    for key in q.connection.scan_iter(f"rq:*:{q.name}"):
        q.connection.delete(key)
    q.connection.srem("rq:queues", q.key)


def _scheduled_at(q: Queue):
    registry = ScheduledJobRegistry(queue=q)
    if "CronTestJob" not in registry.get_job_ids():
        return None
    return registry.get_scheduled_time("CronTestJob")


def _schedule_in(q: Queue, delay: timedelta) -> None:
    q.enqueue_in(delay, CronTestJob._run, job_id="CronTestJob")


def test_first_run_delay():
    assert BaseJob.get_first_run_delay(timedelta(hours=2)) == timedelta(hours=2)
    assert BaseJob.get_first_run_delay(timedelta(hours=6)) == timedelta(hours=6)
    for _ in range(20):
        d = BaseJob.get_first_run_delay(timedelta(days=7))
        assert timedelta(hours=6) <= d <= timedelta(hours=12)
        d = BaseJob.get_first_run_delay(timedelta(hours=8))
        assert timedelta(hours=6) <= d <= timedelta(hours=8)


def test_ensure_schedules_first_run_in_window(queue):
    before = timezone.now()
    CronTestJob.ensure_scheduled()
    at = _scheduled_at(queue)
    assert at is not None
    assert before + timedelta(hours=6) - timedelta(seconds=1) <= at
    assert at <= timezone.now() + timedelta(hours=12) + timedelta(seconds=1)
    job = Job.fetch("CronTestJob", connection=queue.connection)
    assert job.timeout == int(timedelta(days=1).total_seconds()) - 5


def test_ensure_short_interval_waits_one_interval(queue, monkeypatch):
    monkeypatch.setattr(CronTestJob, "interval", timedelta(hours=2))
    before = timezone.now()
    CronTestJob.ensure_scheduled()
    at = _scheduled_at(queue)
    assert at is not None
    assert before + timedelta(hours=2) - timedelta(seconds=1) <= at
    assert at <= timezone.now() + timedelta(hours=2) + timedelta(seconds=1)


def test_ensure_keeps_pending_run(queue):
    _schedule_in(queue, timedelta(hours=1))
    at = _scheduled_at(queue)
    CronTestJob.ensure_scheduled()
    assert _scheduled_at(queue) == at


def test_ensure_keeps_overdue_run(queue):
    _schedule_in(queue, timedelta(seconds=1))
    registry = ScheduledJobRegistry(queue=queue)
    overdue = timezone.now() - timedelta(days=2)
    queue.connection.zadd(registry.key, {"CronTestJob": overdue.timestamp()})
    CronTestJob.ensure_scheduled()
    at = _scheduled_at(queue)
    assert at is not None and at < timezone.now()


def test_ensure_keeps_queued_job(queue):
    queue.enqueue(CronTestJob._run, job_id="CronTestJob")
    CronTestJob.ensure_scheduled()
    assert queue.get_job_ids() == ["CronTestJob"]
    assert _scheduled_at(queue) is None


def test_ensure_replaces_run_beyond_interval(queue):
    _schedule_in(queue, timedelta(days=3))
    CronTestJob.ensure_scheduled()
    at = _scheduled_at(queue)
    assert at is not None
    assert at <= timezone.now() + timedelta(hours=12) + timedelta(seconds=1)


def test_ensure_replaces_orphan_entry(queue):
    _schedule_in(queue, timedelta(hours=1))
    queue.connection.delete(Job.key_for("CronTestJob"))
    CronTestJob.ensure_scheduled()
    at = _scheduled_at(queue)
    assert at is not None
    assert at >= timezone.now() + timedelta(hours=6) - timedelta(seconds=1)
    assert Job.exists("CronTestJob", queue.connection)


def test_ensure_cancels_disabled_job(queue, monkeypatch):
    _schedule_in(queue, timedelta(hours=1))
    monkeypatch.setattr(SiteConfig.system, "disable_cron_jobs", ["CronTestJob"])
    CronTestJob.ensure_scheduled()
    assert _scheduled_at(queue) is None


def test_ensure_cancels_zero_interval_job(queue, monkeypatch):
    _schedule_in(queue, timedelta(hours=1))
    monkeypatch.setattr(CronTestJob, "interval", timedelta(0))
    CronTestJob.ensure_scheduled()
    assert _scheduled_at(queue) is None


def test_run_schedules_next_run_one_interval_later(queue, monkeypatch):
    monkeypatch.setattr(CronTestJob, "run", lambda self: None)
    before = timezone.now()
    CronTestJob._run()
    at = _scheduled_at(queue)
    assert at is not None
    assert before + timedelta(days=1) - timedelta(seconds=1) <= at
