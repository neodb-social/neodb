from typing import Any

from django.conf import settings
from django.db import models
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from users.models import APIdentity


MADE_CARDS_MAX = 20
MADE_CARD_ITEMS_MAX = 10


# the three audiences of a page: everyone (including people who are not signed in), followers, and the owner alone
AUDIENCES = (0, 1, 2)
# in an audience's overrides: True hides every card that has no override of its own, so a card that qualifies later stays out
ALL_HIDDEN = "*"


class WrappedDeck(models.Model):
    """A member's annual summary for one year: which cards each audience sees and how the cards are arranged.

    The member makes the deck once, from 1 December to the end of February after the year; that stores the
    card data of each audience in ``data``, and the cards show only that. Until the end of February the
    site updates it every night unless the member turned ``daily`` off, and the member can update it by
    hand; after that it stays as it is. One link serves everybody: a visitor sees the cards of their own
    audience (0 everyone, 1 followers, 2 the owner), with counts from entries at that level. The order and
    the made cards are the same for all three audiences.

    ``hidden[str(level)]`` holds a card id only when the member overrode the rules for that audience
    (True hides a card the rules show, False shows a card the rules hide); ALL_HIDDEN set to True hides
    every card without an override. ``shown[str(level)]`` is the list the page showed when the owner last
    viewed that audience, and its visitors see those cards and nothing else. For an audience the owner
    has not opened there is no list, and the visitor's browser applies the rules and overrides itself.
    """

    owner = models.ForeignKey(APIdentity, on_delete=models.CASCADE, related_name="+")
    year = models.PositiveSmallIntegerField()
    order = models.JSONField(default=list)
    hidden = models.JSONField(default=dict)
    made = models.JSONField(default=list)
    shown = models.JSONField(default=dict)
    # the background music the owner chose (an id in journal/wrapped_music.py); empty is no music, the default
    music = models.CharField(max_length=40, blank=True, default="")
    timezone = models.CharField(max_length=64, blank=True, default="")
    # the card data of each audience as the last update stored it ({str(level): payload}); the cards show
    # only this, so they stay as they were at that moment; empty until the member makes the deck
    data = models.JSONField(default=dict)
    # when the data was last stored; None until the member makes the deck
    refreshed_time = models.DateTimeField(null=True, blank=True)
    # the member lets the site update the data every night while updates are open (to the end of February)
    daily = models.BooleanField(default=True)
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
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone or settings.TIME_ZONE)
        except ZoneInfoNotFoundError, ValueError:
            return ZoneInfo(settings.TIME_ZONE)

    def overrides(self, level: int) -> dict[str, bool]:
        o = (self.hidden or {}).get(str(level))
        return dict(o) if isinstance(o, dict) else {}

    def shown_for(self, level: int) -> list[str] | None:
        v = (self.shown or {}).get(str(level))
        return list(v) if isinstance(v, list) else None

    def card_hidden(self, level: int, card_id: str) -> bool | None:
        """True or False when the member decided for this card and audience, else None (the rules decide)."""
        o = self.overrides(level)
        if card_id in o:
            return bool(o[card_id])
        return True if o.get(ALL_HIDDEN) is True else None

    def may_show(self, level: int, card_id: str, default: bool) -> bool:
        """Whether this audience may be shown a card: the list the owner last saw for it when there is one,
        else the owner's override, else ``default`` (the server cannot apply the browser's rules)."""
        shown = self.shown_for(level)
        if shown is not None:
            return card_id in shown
        hidden = self.card_hidden(level, card_id)
        return default if hidden is None else not hidden

    def is_off(self, level: int) -> bool:
        """The audience sees no card: everything hidden by the member, or nothing shown at the owner's last view."""
        o = self.overrides(level)
        if o.get(ALL_HIDDEN) is True and not any(
            v is False for k, v in o.items() if k != ALL_HIDDEN
        ):
            return True
        return self.shown_for(level) == []

    def state(self, level: int) -> dict[str, Any]:
        return {
            "order": self.order,
            "hidden": self.overrides(level),
            "made": self.made,
        }
