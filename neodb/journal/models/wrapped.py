from typing import Any

from django.conf import settings
from django.db import models
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from users.models import APIdentity

from .common import VisibilityType

MADE_CARDS_MAX = 20
MADE_CARD_ITEMS_MAX = 10


class WrappedDeck(models.Model):
    """A member's annual summary for one year: who may see the page and how the cards are arranged.

    The cards are computed from the journal each time the page renders; this row keeps only the
    member's choices. ``hidden`` holds a card id only when the member overrode the default (True to
    hide a card the rules show, False to show a card the rules hide). ``shown`` is the list of card
    ids the page showed at the owner's last visit; a visitor sees those cards and nothing else.
    """

    owner = models.ForeignKey(APIdentity, on_delete=models.CASCADE, related_name="+")
    year = models.PositiveSmallIntegerField()
    visibility = models.PositiveSmallIntegerField(
        choices=VisibilityType.choices, default=VisibilityType.Private
    )
    order = models.JSONField(default=list)
    hidden = models.JSONField(default=dict)
    made = models.JSONField(default=list)
    shown = models.JSONField(default=None, null=True)
    timezone = models.CharField(max_length=64, blank=True, default="")
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "year"], name="unique_wrapped_deck"
            )
        ]

    def __str__(self) -> str:
        return f"WrappedDeck:{self.owner_id}:{self.year}"

    @property
    def level(self) -> int:
        return int(self.visibility)

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone or settings.TIME_ZONE)
        except ZoneInfoNotFoundError, ValueError:
            return ZoneInfo(settings.TIME_ZONE)

    def state(self) -> dict[str, Any]:
        return {
            "vis": ["public", "followers", "me"][self.level],
            "order": self.order,
            "hidden": self.hidden,
            "made": self.made,
        }
