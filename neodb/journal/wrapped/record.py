"""The member's stored deck: when it can be made and updated, what it stores, and what a page serves from it."""

import datetime
import logging
import uuid
from collections.abc import Set as AbstractSet
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.signing import b62_decode
from django.db import transaction
from django.db.models import (
    Count,
    Max,
)
from django.utils import timezone, translation

from common.models import SiteConfig
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity

from journal.models import (
    Collection,
    WrappedDeck,
)
from journal.models.wrapped import ALL_HIDDEN, AUDIENCES

from .base import (
    _hidden_people,
    _members,
)
from .aggregates import (
    year_aggregates,
)
from .data import (
    _collections,
    build_payload,
)
from .words import (
    resolve_words,
)

logger = logging.getLogger(__name__)


SOCIAL_CARDS = {"company", "twin", "first"}


def _today() -> datetime.date:
    """The site's date in its own time zone, not the visitor's, so the window has one boundary for everybody
    and for the nightly job; tests patch this to open or close the window for making a deck."""
    return timezone.localdate(timezone=ZoneInfo(settings.TIME_ZONE))


def make_window(year: int) -> tuple[datetime.date, datetime.date]:
    """A member makes the deck of a year once, from 1 December of that year to the end of February after it:
    the first day, and the day after the last."""
    return datetime.date(year, 12, 1), datetime.date(year + 1, 3, 1)


def auto_last(year: int) -> datetime.date:
    """The last day the site updates a deck of this year by itself: 2 January after it."""
    return datetime.date(year + 1, 1, 2)


def auto_update_open(year: int) -> bool:
    """Whether the nightly job still updates the decks of this year: from 1 December to 2 January."""
    if year < 1970 or year >= 9999:
        return False
    return make_window(year)[0] <= _today() <= auto_last(year)


def make_state(year: int) -> str:
    """ "early" before the window for making the deck of this year, "open" in it, "closed" after it."""
    if year < 1970 or year >= 9999:
        return "closed"
    start, end = make_window(year)
    t = _today()
    return "early" if t < start else "open" if t < end else "closed"


def can_make(year: int) -> bool:
    return make_state(year) == "open"


def default_year() -> int:
    """The year the summary page opens on: this year in December, else the year before."""
    t = _today()
    return t.year if t.month == 12 else t.year - 1


def latest_year() -> int:
    """The last year a page can be opened for: this one."""
    return _today().year


def made_data(
    owner: APIdentity, made: list[dict[str, Any]], level: int, tz: ZoneInfo
) -> dict[str, Any]:
    """What the made cards that read the journal show at this level, as the deck stores it: the collections
    of collection cards and the texts of "your words" cards, with their titles as they are now."""
    refs = [
        str((m.get("source") or {}).get("uuid") or "")
        for m in made
        if (m.get("source") or {}).get("type") == "collection"
    ]
    return {
        "collections": _collections(
            owner, level, None, frozenset(), refs, picker=False, store=True
        )
        if refs
        else [],
        "words": resolve_words(owner, made, level, tz),
    }


def _shown_collections(
    stored: list[dict[str, Any]], viewer: Any, hidden: AbstractSet[int]
) -> list[dict[str, Any]]:
    """The stored collections a visitor may see now: another member's only while it is still public and its
    author is neither hidden from the visitor nor gone, and for a signed-out visitor allows them."""
    others = {c["author_id"] for c in stored if c.get("rel") == "liked"}
    authors = (
        dict(
            APIdentity.objects.filter(pk__in=others, deleted__isnull=True).values_list(
                "pk", "anonymous_viewable"
            )
        )
        if others
        else {}
    )
    public = (
        {
            str(u)
            for u in Collection.objects.filter(
                uid__in=[
                    uuid.UUID(int=b62_decode(c["uuid"]))
                    for c in stored
                    if c.get("rel") == "liked"
                ],
                visibility=0,
            ).values_list("uid", flat=True)
        }
        if others
        else set()
    )
    signed_in = bool(viewer and viewer.is_authenticated)
    out = []
    for c in stored:
        if c.get("rel") == "liked":
            a = c.get("author_id")
            if (
                a not in authors
                or a in hidden
                or not (signed_in or authors[a])
                or str(uuid.UUID(int=b62_decode(c["uuid"]))) not in public
            ):
                continue
        out.append({k: v for k, v in c.items() if k != "author_id"})
    return out


def deck_for(owner: APIdentity, year: int, create: bool = False) -> WrappedDeck | None:
    deck = WrappedDeck.objects.filter(owner=owner, year=year).first()
    if deck is None and create:
        # every audience starts with the suggested cards, except those wider than the member's default
        # post visibility: a member who posts to followers by default does not get a public page
        pref = int(owner.preference.default_visibility) if owner.user else 2
        hidden = {str(lv): {ALL_HIDDEN: True} if lv < pref else {} for lv in AUDIENCES}
        deck, _ = WrappedDeck.objects.get_or_create(
            owner=owner, year=year, defaults={"hidden": hidden}
        )
    return deck


def live_stamp(owner: APIdentity) -> str:
    """Changes with the owner's marks (their count and latest edit): the whole-year section's cache key."""
    v = _members().filter(owner=owner).aggregate(n=Count("pk"), t=Max("edited_time"))
    return f"{v['n']}:{v['t'].timestamp() if v['t'] else 0}"


def snapshot(
    owner: APIdentity,
    year: int,
    tz: ZoneInfo,
    instance: tuple[Any, Any] | None = None,
    made: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """The card data of each audience, as the deck stores it: everything the cards show, the made cards'
    collections and texts and every title's name, cover link and numbers included, so later changes to an
    item (a merge, a new cover, a deletion), a collection or a text do not reach the cards. Only the people
    on the social cards are checked again when a page renders, against what its visitor may see. Titles are
    in the owner's language, whether the owner or the nightly job stores the data."""
    instance = instance or year_aggregates(year)
    lang = (owner.user.language if owner.user else "") or settings.LANGUAGE_CODE
    out = {}
    with translation.override(lang):
        for level in AUDIENCES:
            p = build_payload(
                owner,
                year,
                level,
                tz,
                include_friends=True,
                include_kindred=True,
                picker=False,
                hidden=frozenset(),
                instance=instance,
            )
            p.pop("collections", None)
            p["made_data"] = made_data(owner, made or [], level, tz)
            out[str(level)] = p
    return out


def refresh_deck(deck: WrappedDeck, instance: tuple[Any, Any] | None = None) -> bool:
    """Store the card data as it is now; True when it changed. The time is stored either way, as the page
    tells the owner when the data was last updated."""
    data = snapshot(deck.owner, deck.year, deck.tzinfo, instance, deck.made)
    with transaction.atomic():
        # the read above takes a while, and a save may have changed the made cards meanwhile; the row is
        # locked for the write, and the made cards' data is read again for them as they are now
        locked = WrappedDeck.objects.select_for_update().get(pk=deck.pk)
        if locked.made != deck.made:
            for key in data:
                data[key]["made_data"] = made_data(
                    deck.owner, locked.made, int(key), deck.tzinfo
                )
        fields = ["refreshed_time"]
        changed = data != locked.data
        if changed:
            locked.data = data
            fields.append("data")
        locked.refreshed_time = timezone.now()
        locked.save(update_fields=fields)
    deck.made, deck.data, deck.refreshed_time = (
        locked.made,
        locked.data,
        locked.refreshed_time,
    )
    return changed


def stored_payload(
    deck: WrappedDeck,
    level: int,
    viewer: Any = None,
    is_owner: bool = False,
    picker: bool = False,
) -> dict[str, Any]:
    """The cards' data for one audience as the last update stored it, with what depends on this page applied
    now: the members this visitor must not see leave the social cards and the made cards' collections, and
    the social and kindred data reach only an audience whose cards may show them. ``picker``: the owner's
    Make a card dialog also gets their latest own and liked collections, read now."""
    p = dict((deck.data or {}).get(str(level)) or {})
    owner = deck.owner
    friends = (p.get("friends") or []) if friends_allowed(deck, level, is_owner) else []
    kin = p.get("kindred") if kindred_allowed(deck, level, is_owner) else None
    # other people as they are now, as the stored data names them: not those this visitor must not see, nor
    # deleted accounts or members the owner blocks; the kindred member also only while still discoverable
    hidden = _hidden_people(viewer)
    named = {f.get("id") for f in friends} | ({kin.get("id")} if kin else set())
    named.discard(None)
    present = (
        set(
            APIdentity.objects.filter(pk__in=named, deleted__isnull=True).values_list(
                "pk", flat=True
            )
        )
        - hidden
        - set(owner.ignoring)
        if named
        else set()
    )
    p["friends"] = [
        {k: v for k, v in f.items() if k != "id"}
        for f in friends
        if f.get("id") in present
    ]
    p["kindred"] = (
        {k: v for k, v in kin.items() if k != "id"}
        if kin and kin.get("id") in present and _discoverable(kin["id"])
        else None
    )
    made = p.pop("made_data", None) or {}
    cols = _shown_collections(made.get("collections") or [], viewer, hidden)
    if picker:
        have = {c["uuid"] for c in cols}
        cols += [
            c
            for c in _collections(owner, level, viewer, hidden, None, True)
            if c["uuid"] not in have
        ]
    p["collections"] = cols
    p["words"] = made.get("words") or {}
    return p


def _discoverable(identity_id: int) -> bool:
    """Whether a member may still appear as someone's kindred member: discoverable, and not excluded by the site."""
    if not TakaheIdentity.objects.filter(pk=identity_id, discoverable=True).exists():
        return False
    excluded = SiteConfig.system.discover_exclude_posts_from
    return not (
        excluded and identity_id in Takahe.get_identity_ids_by_handles(excluded)
    )


def owner_years(owner: APIdentity) -> list[int]:
    """The years the owner can open from the year menu: those with a deck, and the one whose deck can be made now."""
    years = set(
        WrappedDeck.objects.filter(
            owner=owner, refreshed_time__isnull=False
        ).values_list("year", flat=True)
    )
    years.update(y for y in (latest_year(), latest_year() - 1) if can_make(y))
    return sorted(years, reverse=True)


def friends_allowed(deck: WrappedDeck | None, level: int, is_owner: bool) -> bool:
    """A visitor gets followed members' data only when a social card can be shown to their audience."""
    if is_owner:
        return True
    return bool(deck) and any(deck.may_show(level, c, True) for c in SOCIAL_CARDS)


def kindred_allowed(deck: WrappedDeck | None, level: int, is_owner: bool) -> bool:
    """The kindred card names a member the owner does not follow, so by default only the owner sees it;
    another audience gets its data only when the owner turned the card on for that audience."""
    if is_owner:
        return True
    return bool(deck) and deck.may_show(level, "kindred", False)
