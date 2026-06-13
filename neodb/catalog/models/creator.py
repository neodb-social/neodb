import re
from typing import TYPE_CHECKING, Iterable

from django.db import models
from django.utils.translation import gettext_lazy as _

from .item import Item

if TYPE_CHECKING:
    from users.models import User


class VerifiedCreator(models.Model):
    """A user verified as creator of an item, by matching their fediverse
    identity in the item's external source description, or manually by admin.

    Only rows in VERIFIED state grant edit permission and show up on profile.
    """

    class State(models.TextChoices):
        PENDING = "pending", _("pending")
        VERIFIED = "verified", _("verified")
        FAILED = "failed", _("failed")

    class FailureReason(models.TextChoices):
        NO_FEED = "no_feed", _("no feed url available for this item")
        FETCH_FAILED = "fetch_failed", _("unable to fetch or parse the feed")
        NO_MATCH = "no_match", _("no matching identity found in the feed description")

    if TYPE_CHECKING:
        item_id: int
        owner_id: int
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="verified_creators"
    )
    owner = models.ForeignKey(
        "users.APIdentity", on_delete=models.CASCADE, related_name="verified_works"
    )
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.PENDING
    )
    matched = models.CharField(max_length=1000, blank=True, default="")
    failure_reason = models.CharField(
        max_length=20, choices=FailureReason.choices, blank=True, default=""
    )
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["item", "owner"]]

    def __str__(self):
        return f"{self.pk}:{self.item_id}:{self.owner_id}:{self.state}"

    @property
    def failure_display(self) -> str:
        return (
            str(self.FailureReason(self.failure_reason).label)
            if self.failure_reason
            else ""
        )


def creator_identity_candidates(user: "User") -> list[str]:
    """Identifiers that prove ownership when found in a feed description."""
    identity = user.identity
    candidates = [f"@{identity.full_handle}"]
    actor_uri = identity.actor_uri
    if actor_uri:
        candidates.append(actor_uri)
    if user.mastodon:
        candidates.append(f"@{user.mastodon.handle}")
        if user.mastodon.url:
            candidates.append(user.mastodon.url)
    if user.bluesky and user.bluesky.handle:
        candidates.append(f"@{user.bluesky.handle}")
    return candidates


def match_creator_identity(
    descriptions: Iterable[str], candidates: Iterable[str]
) -> str | None:
    """Return the first candidate identifier found in any description.

    Matching is case-insensitive and boundary-guarded on both sides, so that
    e.g. "@a@b.com" matches neither inside "@a@b.com.evil" nor inside
    "@x@a@b.com".
    """
    texts = [d.lower() for d in descriptions if d]
    for candidate in candidates:
        c = candidate.strip().lower()
        if not c:
            continue
        if "://" in c:
            left, right = r"(?<![\w\-./])", r"(?![\w\-./])"
        else:
            left, right = r"(?<![\w\-.@])", r"(?![\w\-.])"
        pattern = left + re.escape(c) + right
        if any(re.search(pattern, t) for t in texts):
            return candidate
    return None
