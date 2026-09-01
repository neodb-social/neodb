from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import django_rq
from django.db import models, transaction
from django.utils import timezone

from common.models.durable_dispatch import DurableDispatch

DEFAULT_LEASE_DURATION = timedelta(minutes=5)
DEFAULT_MAX_RETRY_DELAY = timedelta(hours=1)
MAX_ERROR_TEXT_LENGTH = 500


@dataclass(frozen=True)
class DispatchLease:
    dispatch_id: int
    lease_token: str
    queue: str
    responsibility_ref: str


@dataclass(frozen=True)
class ReconciliationResult:
    claimed: int
    dispatched: int
    enqueue_errors: int


def create_dispatch(
    responsibility_ref: str,
    *,
    queue: str = "cron",
    max_attempts: int = 5,
    next_attempt_at: datetime | None = None,
) -> DurableDispatch:
    """Create only the delivery bookkeeping row.

    Domain code should call this inside the same transaction as its own
    intent/state write. The reference is deliberately opaque and is not
    interpreted or generated here.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    return DurableDispatch.objects.create(
        responsibility_ref=responsibility_ref,
        queue=queue,
        max_attempts=max_attempts,
        next_attempt_at=next_attempt_at or timezone.now(),
    )


def claim_due_dispatches(
    *,
    limit: int = 100,
    now: datetime | None = None,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> list[DispatchLease]:
    """Atomically lease due rows, with PostgreSQL duplicate-worker safety."""

    if limit < 1:
        return []
    if lease_duration <= timedelta(0):
        raise ValueError("lease_duration must be positive")

    now = now or timezone.now()
    leases: list[DispatchLease] = []
    with transaction.atomic():
        rows = list(
            DurableDispatch.objects.select_for_update(skip_locked=True)
            .filter(
                state=DurableDispatch.State.READY,
                next_attempt_at__lte=now,
                lease_expires_at__isnull=True,
            )
            .filter(attempt_count__lt=models.F("max_attempts"))
            .order_by("next_attempt_at", "id")[:limit]
        )
        for dispatch in rows:
            token = uuid4().hex
            dispatch.state = DurableDispatch.State.CLAIMED
            dispatch.attempt_count += 1
            dispatch.last_attempt_at = now
            dispatch.lease_expires_at = now + lease_duration
            dispatch.lease_token = token
            dispatch.save(
                update_fields=[
                    "state",
                    "attempt_count",
                    "last_attempt_at",
                    "lease_expires_at",
                    "lease_token",
                    "updated_at",
                ]
            )
            leases.append(
                DispatchLease(
                    dispatch_id=dispatch.pk,
                    lease_token=token,
                    queue=dispatch.queue,
                    responsibility_ref=dispatch.responsibility_ref,
                )
            )
    return leases


def recover_expired_claims(*, now: datetime | None = None, limit: int = 100) -> int:
    """Move abandoned claims to owner observation, never to blind retry."""

    if limit < 1:
        return 0
    now = now or timezone.now()
    recovered = 0
    with transaction.atomic():
        rows = list(
            DurableDispatch.objects.select_for_update(skip_locked=True)
            .filter(
                state=DurableDispatch.State.CLAIMED,
                lease_expires_at__lte=now,
            )
            .order_by("lease_expires_at", "id")[:limit]
        )
        for dispatch in rows:
            dispatch.state = DurableDispatch.State.OBSERVATION
            dispatch.next_attempt_at = None
            dispatch.lease_expires_at = None
            dispatch.lease_token = None
            dispatch.last_outcome = DurableDispatch.Outcome.LEASE_EXPIRED
            dispatch.last_error_category = "lease_expired"
            dispatch.last_error_text = (
                "Claim expired; owner observation is required before another effect."
            )
            dispatch.last_error_at = now
            dispatch.save(
                update_fields=[
                    "state",
                    "next_attempt_at",
                    "lease_expires_at",
                    "lease_token",
                    "last_outcome",
                    "last_error_category",
                    "last_error_text",
                    "last_error_at",
                    "updated_at",
                ]
            )
            recovered += 1
    return recovered


def reconcile_due_dispatches(
    dispatcher: Callable[[DispatchLease], Any],
    *,
    limit: int = 100,
    now: datetime | None = None,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> ReconciliationResult:
    """Claim due rows and let the domain owner enqueue its own RQ job.

    The callback owns the domain job and any read-before-repeat policy. An
    enqueue exception is recorded as transport metadata while the lease is
    retained until expiry; it is intentionally not converted into a safe
    remote-effect retry.
    """

    leases = claim_due_dispatches(
        limit=limit,
        now=now,
        lease_duration=lease_duration,
    )
    dispatched = 0
    enqueue_errors = 0
    for lease in leases:
        try:
            dispatcher(lease)
        except Exception:  # noqa: BLE001 - one transport failure must not stop the sweep
            enqueue_errors += 1
            record_enqueue_error(lease)
        else:
            dispatched += 1
    return ReconciliationResult(len(leases), dispatched, enqueue_errors)


def enqueue_claimed_dispatch(
    lease: DispatchLease,
    job: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Publish a domain-owned job to an existing NeoDB RQ queue.

    The RQ job id is transport-scoped to this lease. It is not a business
    operation key. ``django-rq`` defers publication to transaction commit when
    called inside a transaction, while the durable row remains the recovery
    authority if that callback is lost.
    """

    kwargs.setdefault(
        "job_id", f"durable-dispatch-{lease.dispatch_id}-{lease.lease_token}"
    )
    return django_rq.get_queue(lease.queue, commit_mode="on_db_commit").enqueue(
        job,
        lease.dispatch_id,
        lease.lease_token,
        *args,
        **kwargs,
    )


def claim_is_current(
    dispatch_id: int, lease_token: str, *, now: datetime | None = None
) -> bool:
    now = now or timezone.now()
    return DurableDispatch.objects.filter(
        pk=dispatch_id,
        state=DurableDispatch.State.CLAIMED,
        lease_token=lease_token,
        lease_expires_at__gt=now,
    ).exists()


def renew_claim(
    dispatch_id: int,
    lease_token: str,
    *,
    now: datetime | None = None,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> bool:
    if lease_duration <= timedelta(0):
        raise ValueError("lease_duration must be positive")
    now = now or timezone.now()
    updated = DurableDispatch.objects.filter(
        pk=dispatch_id,
        state=DurableDispatch.State.CLAIMED,
        lease_token=lease_token,
        lease_expires_at__gt=now,
    ).update(lease_expires_at=now + lease_duration, updated_at=now)
    return updated == 1


def mark_safe_retry(
    dispatch_id: int,
    lease_token: str,
    *,
    error_category: str,
    error_text: str = "",
    now: datetime | None = None,
    base_delay: timedelta = timedelta(minutes=1),
    max_delay: timedelta = DEFAULT_MAX_RETRY_DELAY,
) -> bool:
    """Make a current attempt retryable only after owner proof of no effect."""

    if base_delay < timedelta(0) or max_delay < base_delay:
        raise ValueError("retry delays must be non-negative and ordered")
    now = now or timezone.now()
    with transaction.atomic():
        dispatch = _current_claim(dispatch_id, lease_token, now)
        if dispatch is None:
            return False
        dispatch.last_outcome = DurableDispatch.Outcome.SAFE_RETRY
        dispatch.last_error_category = _bounded(error_category, 40)
        dispatch.last_error_text = _bounded(error_text)
        dispatch.last_error_at = now
        dispatch.lease_expires_at = None
        dispatch.lease_token = None
        if dispatch.attempt_count >= dispatch.max_attempts:
            dispatch.state = DurableDispatch.State.OBSERVATION
            dispatch.next_attempt_at = None
        else:
            delay = min(
                max_delay,
                base_delay * (2 ** min(max(dispatch.attempt_count - 1, 0), 20)),
            )
            dispatch.state = DurableDispatch.State.READY
            dispatch.next_attempt_at = now + delay
        dispatch.save(
            update_fields=[
                "state",
                "next_attempt_at",
                "lease_expires_at",
                "lease_token",
                "last_outcome",
                "last_error_category",
                "last_error_text",
                "last_error_at",
                "updated_at",
            ]
        )
    return True


def schedule_safe_retry_after_observation(
    dispatch_id: int,
    *,
    reason: str,
    now: datetime | None = None,
) -> bool:
    """Release an ambiguous row only after the domain owner read/repairs it."""

    now = now or timezone.now()
    updated = DurableDispatch.objects.filter(
        pk=dispatch_id,
        state=DurableDispatch.State.OBSERVATION,
        attempt_count__lt=models.F("max_attempts"),
    ).update(
        state=DurableDispatch.State.READY,
        next_attempt_at=now,
        last_outcome=DurableDispatch.Outcome.SAFE_RETRY,
        last_error_category="owner_repaired",
        last_error_text=_bounded(reason),
        last_error_at=now,
        updated_at=now,
    )
    return updated == 1


def mark_ambiguous(
    dispatch_id: int,
    lease_token: str,
    *,
    error_category: str = "ambiguous_result",
    error_text: str = "Owner observation required before another effect.",
    now: datetime | None = None,
) -> bool:
    now = now or timezone.now()
    with transaction.atomic():
        dispatch = _current_claim(dispatch_id, lease_token, now)
        if dispatch is None:
            return False
        dispatch.state = DurableDispatch.State.OBSERVATION
        dispatch.next_attempt_at = None
        dispatch.lease_expires_at = None
        dispatch.lease_token = None
        dispatch.last_outcome = DurableDispatch.Outcome.AMBIGUOUS
        dispatch.last_error_category = _bounded(error_category, 40)
        dispatch.last_error_text = _bounded(error_text)
        dispatch.last_error_at = now
        dispatch.save(
            update_fields=[
                "state",
                "next_attempt_at",
                "lease_expires_at",
                "lease_token",
                "last_outcome",
                "last_error_category",
                "last_error_text",
                "last_error_at",
                "updated_at",
            ]
        )
    return True


def mark_terminal(
    dispatch_id: int,
    lease_token: str,
    *,
    outcome: str,
    now: datetime | None = None,
) -> bool:
    if outcome not in {
        DurableDispatch.Outcome.KNOWN_SUCCESS,
        DurableDispatch.Outcome.OWNER_REJECTED,
    }:
        raise ValueError("terminal outcome must be known success or owner rejection")
    now = now or timezone.now()
    with transaction.atomic():
        dispatch = _current_claim(dispatch_id, lease_token, now)
        if dispatch is None:
            return False
        dispatch.state = DurableDispatch.State.RETIRED
        dispatch.next_attempt_at = None
        dispatch.lease_expires_at = None
        dispatch.lease_token = None
        dispatch.last_outcome = outcome
        dispatch.save(
            update_fields=[
                "state",
                "next_attempt_at",
                "lease_expires_at",
                "lease_token",
                "last_outcome",
                "updated_at",
            ]
        )
    return True


def record_enqueue_error(lease: DispatchLease, *, now: datetime | None = None) -> bool:
    """Record transport failure without granting retry eligibility."""

    now = now or timezone.now()
    updated = DurableDispatch.objects.filter(
        pk=lease.dispatch_id,
        state=DurableDispatch.State.CLAIMED,
        lease_token=lease.lease_token,
    ).update(
        last_outcome=DurableDispatch.Outcome.ENQUEUE_ERROR,
        last_error_category="enqueue_error",
        last_error_text="RQ enqueue raised; lease recovery must observe before repeat.",
        last_error_at=now,
        updated_at=now,
    )
    return updated == 1


def _current_claim(
    dispatch_id: int, lease_token: str, now: datetime
) -> DurableDispatch | None:
    return (
        DurableDispatch.objects.select_for_update()
        .filter(
            pk=dispatch_id,
            state=DurableDispatch.State.CLAIMED,
            lease_token=lease_token,
            lease_expires_at__gt=now,
        )
        .first()
    )


def _bounded(value: str, limit: int = MAX_ERROR_TEXT_LENGTH) -> str:
    return " ".join(str(value).split())[:limit]
