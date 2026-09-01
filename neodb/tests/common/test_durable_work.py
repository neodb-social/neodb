from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

import pytest
from django.db import close_old_connections, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from common import durable_work
from common.durable_work import (
    claim_due_dispatches,
    claim_is_current,
    create_dispatch,
    enqueue_claimed_dispatch,
    mark_ambiguous,
    mark_safe_retry,
    mark_terminal,
    reconcile_due_dispatches,
    recover_expired_claims,
    schedule_safe_retry_after_observation,
)
from common.models import DurableDispatch, SiteConfig


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_atomic_product_state_and_responsibility_commit_together():
    with transaction.atomic():
        SiteConfig.objects.update_or_create(pk=1, defaults={"data": {"m3": True}})
        dispatch = create_dispatch("test:atomic-responsibility")

    assert SiteConfig.objects.filter(pk=1).exists()
    assert DurableDispatch.objects.filter(pk=dispatch.pk).exists()

    with pytest.raises(RuntimeError), transaction.atomic():
        SiteConfig.objects.update_or_create(pk=1, defaults={"data": {"rolled": True}})
        create_dispatch("test:rolled-back")
        raise RuntimeError("rollback exemplar")
    assert not DurableDispatch.objects.filter(
        responsibility_ref="test:rolled-back"
    ).exists()


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_common_bookkeeping_migration_upgrades_from_0003():
    connection = connections["default"]
    executor = MigrationExecutor(connection)
    executor.migrate([("common", "0003_default_user_icon_png")])

    assert DurableDispatch._meta.db_table not in connection.introspection.table_names()

    executor = MigrationExecutor(connection)
    executor.migrate([("common", "0004_durabledispatch")])
    assert DurableDispatch._meta.db_table in connection.introspection.table_names()


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_reconciler_discovers_committed_work_without_on_commit_callback():
    dispatch = create_dispatch("test:queue-loss", queue="ap")
    seen = []

    result = reconcile_due_dispatches(seen.append)

    assert result.claimed == result.dispatched == 1
    assert result.enqueue_errors == 0
    assert seen[0].dispatch_id == dispatch.pk
    assert claim_is_current(dispatch.pk, seen[0].lease_token)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_enqueue_uses_the_claimed_lease_on_an_existing_queue(monkeypatch):
    class RecordingQueue:
        def __init__(self):
            self.calls = []

        def enqueue(self, job, *args, **kwargs):
            self.calls.append((job, args, kwargs))
            return "rq-job"

    queue = RecordingQueue()
    monkeypatch.setattr(
        "common.durable_work.django_rq.get_queue",
        lambda name, commit_mode: queue,
    )
    dispatch = create_dispatch("test:rq-transport", queue="ap")
    lease = claim_due_dispatches()[0]

    assert enqueue_claimed_dispatch(lease, "domain_job", "owner-arg") == "rq-job"
    assert queue.calls == [
        (
            "domain_job",
            (dispatch.pk, lease.lease_token, "owner-arg"),
            {"job_id": f"durable-dispatch-{dispatch.pk}-{lease.lease_token}"},
        )
    ]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_enqueue_exception_is_recorded_without_automatic_safe_retry():
    dispatch = create_dispatch("test:rq-error")

    def fail(_lease):
        raise RuntimeError("test-only transport failure")

    result = reconcile_due_dispatches(fail)
    dispatch.refresh_from_db()

    assert result.enqueue_errors == 1
    assert dispatch.state == DurableDispatch.State.CLAIMED
    assert dispatch.last_outcome == DurableDispatch.Outcome.ENQUEUE_ERROR
    assert not dispatch.retry_eligible


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_duplicate_workers_cannot_claim_active_lease_twice():
    dispatch = create_dispatch("test:duplicate-claim")

    first = claim_due_dispatches()
    second = claim_due_dispatches()

    assert [lease.dispatch_id for lease in first] == [dispatch.pk]
    assert second == []


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_concurrent_postgres_workers_skip_a_locked_dispatch(monkeypatch):
    dispatch = create_dispatch("test:concurrent-claim")
    first_selected = Event()
    release_first = Event()
    gate_lock = Lock()
    first_call = [True]
    original_uuid4 = durable_work.uuid4

    def block_first_token_generation():
        with gate_lock:
            should_block = first_call[0]
            first_call[0] = False
        if should_block:
            first_selected.set()
            assert release_first.wait(timeout=10)
        return original_uuid4()

    monkeypatch.setattr(durable_work, "uuid4", block_first_token_generation)

    def worker():
        close_old_connections()
        try:
            return claim_due_dispatches()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        assert first_selected.wait(timeout=10)
        second = pool.submit(worker)
        assert second.result(timeout=10) == []
        release_first.set()
        first_leases = first.result(timeout=10)

    assert [lease.dispatch_id for lease in first_leases] == [dispatch.pk]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_expired_worker_claim_is_recoverable_but_requires_observation():
    dispatch = create_dispatch("test:worker-restart")
    claimed_at = timezone.now()
    lease = claim_due_dispatches(
        now=claimed_at,
        lease_duration=timedelta(seconds=1),
    )[0]

    recovered = recover_expired_claims(now=claimed_at + timedelta(seconds=2))
    dispatch.refresh_from_db()

    assert recovered == 1
    assert dispatch.state == DurableDispatch.State.OBSERVATION
    assert dispatch.last_outcome == DurableDispatch.Outcome.LEASE_EXPIRED
    assert not dispatch.retry_eligible
    assert not claim_is_current(dispatch.pk, lease.lease_token)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_safe_retry_is_bounded_and_ambiguous_result_is_not_retried():
    safe = create_dispatch("test:safe-retry", max_attempts=2)
    safe_lease = claim_due_dispatches()[0]
    assert mark_safe_retry(
        safe.pk,
        safe_lease.lease_token,
        error_category="pre_effect_transport",
        error_text="connection failed before request",
        base_delay=timedelta(seconds=10),
        max_delay=timedelta(minutes=1),
    )
    safe.refresh_from_db()
    assert safe.state == DurableDispatch.State.READY
    assert safe.last_outcome == DurableDispatch.Outcome.SAFE_RETRY
    assert not safe.retry_eligible
    assert safe.next_attempt_at > timezone.now()

    ambiguous = create_dispatch("test:ambiguous")
    ambiguous_lease = claim_due_dispatches()[0]
    assert mark_ambiguous(ambiguous.pk, ambiguous_lease.lease_token)
    ambiguous.refresh_from_db()
    assert ambiguous.state == DurableDispatch.State.OBSERVATION
    assert ambiguous.last_outcome == DurableDispatch.Outcome.AMBIGUOUS
    assert reconcile_due_dispatches(lambda lease: None).claimed == 0
    assert schedule_safe_retry_after_observation(
        ambiguous.pk, reason="owner read confirmed no effect"
    )
    ambiguous.refresh_from_db()
    assert ambiguous.retry_eligible


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_stale_duplicate_cannot_retire_new_claim_and_terminal_outcomes_are_distinct():
    dispatch = create_dispatch("test:converges")
    old_lease = claim_due_dispatches(lease_duration=timedelta(seconds=1))[0]
    recover_expired_claims(now=timezone.now() + timedelta(seconds=2))
    dispatch.refresh_from_db()
    assert schedule_safe_retry_after_observation(
        dispatch.pk, reason="duplicate queue item had no accepted effect"
    )
    new_lease = claim_due_dispatches()[0]

    assert not mark_terminal(
        dispatch.pk,
        old_lease.lease_token,
        outcome=DurableDispatch.Outcome.KNOWN_SUCCESS,
    )
    assert mark_terminal(
        dispatch.pk,
        new_lease.lease_token,
        outcome=DurableDispatch.Outcome.OWNER_REJECTED,
    )
    dispatch.refresh_from_db()
    assert dispatch.state == DurableDispatch.State.RETIRED
    assert dispatch.last_outcome == DurableDispatch.Outcome.OWNER_REJECTED
