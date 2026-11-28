"""Shared parts of the annual summary data: the categories, the queries every part runs, and titles in short form."""

import datetime
import hashlib
import logging
import re
import uuid
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import (
    QuerySet,
    prefetch_related_objects,
)
from django.urls import reverse

from catalog.models import (
    Item,
    ItemCredit,
    TVSeason,
    item_content_types,
)
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity

from journal.models import (
    Rating,
    ShelfMember,
)

logger = logging.getLogger(__name__)


CATEGORIES = ["book", "movie", "tv", "music", "game", "podcast", "performance"]


BULK_DAY = 30


EXCERPT_CHARS = 200


# credit roles the People card understands; other roles map onto them or are left out
CREDIT_ROLES = {
    "director": "director",
    "author": "author",
    "playwright": "author",
    "artist": "artist",
    "composer": "artist",
    "host": "host",
    "developer": "developer",
    "designer": "designer",
    "actor": "actor",
    "performer": "actor",
}


ACTORS_PER_ITEM = 5


def _members() -> QuerySet[ShelfMember]:
    # the default manager adds rating and comment subqueries, which grouped queries must not carry
    return QuerySet(model=ShelfMember)


def year_range(year: int, tz: ZoneInfo) -> tuple[datetime.datetime, datetime.datetime]:
    return (
        datetime.datetime(year, 1, 1, tzinfo=tz),
        datetime.datetime(year + 1, 1, 1, tzinfo=tz),
    )


def _category_of(ctype_id: int, ctypes: dict[int, str]) -> str | None:
    c = ctypes.get(ctype_id)
    return c if c in CATEGORIES else None


def _ctype_categories() -> dict[int, str]:
    return {ct: str(cls.category) for cls, ct in item_content_types().items()}


def _release_year(item: Item) -> int | None:
    for attr in ("pub_year", "year"):
        try:
            v = getattr(item, attr, None)
        except Exception:
            v = None
        if isinstance(v, int) and 1000 < v < 3000:
            return v
    for attr in ("release_date", "opening_date"):
        v = getattr(item, attr, None)
        m = re.match(r"^(\d{4})", str(v or ""))
        if m:
            return int(m.group(1))
    return None


def _drop_spoilers(s: str) -> str:
    """The text without its >!spoilers!<, and without anything after a spoiler that is not closed.

    str.find keeps this linear; a lazy regex scans to the end again for each unclosed >!."""
    out, pos = [], 0
    while (start := s.find(">!", pos)) >= 0:
        out.append(s[pos:start])
        end = s.find("!<", start + 2)
        if end < 0:
            return "".join(out)
        out.append(" ")
        pos = end + 2
    out.append(s[pos:])
    return "".join(out)


def plain_excerpt(md: str, size: int = EXCERPT_CHARS) -> str:
    """A short plain-text start of a review: no spoilers, images or markdown marks.

    The text is cut first to a length the excerpt can need, and each pattern is bounded, so a long text
    with many unclosed marks still takes linear time."""
    s = _drop_spoilers((md or "")[: size * 10 + 1000])
    s = re.sub(r"!\[[^\]]{0,500}\]\([^)]{0,2000}\)", " ", s)
    s = re.sub(r"\[([^\]]{0,500})\]\([^)]{0,2000}\)", r"\1", s)
    s = re.sub(r"<[^>]{1,500}>", " ", s)
    s = re.sub(r"[#*_>`~]+", "", s)
    return re.sub(r"\s+", " ", s).strip()[:size]


def _member(identity: APIdentity) -> dict[str, Any]:
    ti = identity.takahe_identity if identity.local else None
    icon = ti.icon if ti else None
    return {
        "name": identity.display_name,
        "handle": "@" + identity.full_handle,
        "url": settings.SITE_INFO["site_url"].rstrip("/") + identity.url,
        # the profile picture from this site's origin, for the corner of every card; None when the member has not set one.
        # The file name changes with each new picture, so it versions the link.
        "avatar_card": (
            reverse("journal:wrapped_avatar", args=[identity.pk])
            + "?v="
            + hashlib.sha1(icon.name.encode()).hexdigest()[:10]
            if icon
            else None
        ),
    }


def _items_by_id(ids: list[int]) -> dict[int, Item]:
    # the polymorphic manager returns subclasses in one query per subclass, so jsondata fields resolve;
    # a season's or an episode's title reads its parent, so the parents are loaded in one go
    items = list(Item.objects.filter(pk__in=ids))
    Item.prefetch_parent_items(items)
    # a bare "Season 1" counts its show's seasons when the show has no season_count: one query for all of them
    shows = [
        i.show
        for i in items
        if isinstance(i, TVSeason) and i.show and not i.show.season_count
    ]
    if shows:
        prefetch_related_objects(shows, "seasons")
    return {i.pk: i for i in items}


def _credits(ids: list[int]) -> dict[int, list[list[str]]]:
    out: dict[int, list[list[str]]] = defaultdict(list)
    actors: dict[int, int] = defaultdict(int)
    for item_id, role, name in ItemCredit.objects.filter(item_id__in=ids).values_list(
        "item_id", "role", "name"
    ):
        r = CREDIT_ROLES.get(role)
        if not r or not name:
            continue
        if r == "actor":
            actors[item_id] += 1
            if actors[item_id] > ACTORS_PER_ITEM:
                continue
        out[item_id].append([r, name])
    return out


def _grades(
    owner: APIdentity, ids: list[int], level: int | None = None
) -> dict[int, int]:
    """The owner's grade of each title, from ratings visible at this level (all of them when None)."""
    qs = Rating.objects.filter(owner=owner, item_id__in=ids, grade__isnull=False)
    if level is not None:
        qs = qs.filter(visibility__lte=level)
    return dict(qs.values_list("item_id", "grade"))


def _hidden_people(viewer: Any) -> set[int]:
    """Members this visitor must not see named or counted on someone else's cards: moderated accounts
    they do not follow, and anyone they block or mute or who blocks them, as the journal's own rule for
    other people's entries does. Whether a member lets signed-out visitors open their pages does not
    matter here: that covers only the member's own pages, not being named on someone else's."""
    restricted = set(
        TakaheIdentity.objects.filter(restriction__gt=0).values_list("pk", flat=True)
    )
    if not viewer or not viewer.is_authenticated:
        return restricted
    me = viewer.identity
    return (restricted - set(me.following) - {me.pk}) | set(me.ignoring)


def item_ref(item: Item) -> dict[str, Any]:
    """A title in short form, for collection cards and the Make a card picker."""
    return {
        "u": item.url,
        "t": item.display_title,
        "cov": item.display_cover_image_url,
        "c": str(item.category),
    }


def _card_item(it: Item) -> dict[str, Any]:
    """A title as the cards draw it: name, link, cover and shelf."""
    return {
        "title": it.display_title,
        "url": it.absolute_url,
        "cover": it.display_cover_image_url,
        "category": str(it.category),
    }


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False
