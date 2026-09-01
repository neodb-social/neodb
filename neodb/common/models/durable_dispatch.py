from typing import ClassVar

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DurableDispatch(models.Model):
    """Bookkeeping for a domain-owned responsibility that needs delivery.

    This row deliberately does not contain business intent, remote-effect
    results, or an idempotency key. ``responsibility_ref`` is an opaque locator
    supplied by the domain owner; the row only records whether its next
    delivery attempt is schedulable and what happened to the lease.
    """

    QUEUE_CHOICES = (
        ("mastodon", "Mastodon"),
        ("export", "Export"),
        ("import", "Import"),
        ("fetch", "Fetch"),
        ("crawl", "Crawl"),
        ("ap", "ActivityPub"),
        ("cron", "Cron"),
    )

    class State(models.TextChoices):
        READY = "ready", _("Ready")
        CLAIMED = "claimed", _("Claimed")
        OBSERVATION = "observation", _("Needs observation")
        RETIRED = "retired", _("Retired")

    class Outcome(models.TextChoices):
        KNOWN_SUCCESS = "known_success", _("Known success")
        OWNER_REJECTED = "owner_rejected", _("Owner rejected")
        SAFE_RETRY = "safe_retry", _("Safe retry")
        AMBIGUOUS = "ambiguous", _("Ambiguous")
        LEASE_EXPIRED = "lease_expired", _("Lease expired")
        ENQUEUE_ERROR = "enqueue_error", _("Enqueue error")

    responsibility_ref = models.CharField(max_length=255)
    queue = models.CharField(max_length=16, choices=QUEUE_CHOICES, default="cron")
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.READY,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(
        default=timezone.now,
        null=True,
        blank=True,
    )
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=32, null=True, blank=True, unique=True)
    last_outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        blank=True,
        default="",
    )
    last_error_category = models.CharField(max_length=40, blank=True, default="")
    last_error_text = models.CharField(max_length=500, blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["state", "next_attempt_at"],
                name="durdispatch_ready_idx",
            ),
            models.Index(
                fields=["state", "lease_expires_at"],
                name="durdispatch_lease_idx",
            ),
            models.Index(
                fields=["responsibility_ref"],
                name="durdispatch_ref_idx",
            ),
        ]
        ordering: ClassVar[list[str]] = ["next_attempt_at", "id"]

    def __str__(self) -> str:
        return f"{self.responsibility_ref} ({self.state})"

    @property
    def retry_eligible(self) -> bool:
        return bool(
            self.state == self.State.READY
            and self.attempt_count < self.max_attempts
            and self.next_attempt_at
            and self.next_attempt_at <= timezone.now()
        )

    @property
    def apparently_stuck(self) -> bool:
        return bool(
            self.state == self.State.CLAIMED
            and self.lease_expires_at
            and self.lease_expires_at <= timezone.now()
        )
