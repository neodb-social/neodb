"""Data for the annual summary deck.

``build_payload`` reads one member's year at one visibility level (0 public, 1 followers only,
2 only the member) and returns the dict the deck script renders from. Only pieces at or below
that level go in, so a public page never carries a followers-only comment or a private day.
Every query is batched: the number of queries does not grow with the number of titles.
"""

import datetime
import logging
import uuid
from collections import defaultdict
from collections.abc import Set as AbstractSet
from typing import Any
from zoneinfo import ZoneInfo

from django.core.signing import b62_decode
from django.db import connection
from django.db.models import (
    Count,
    F,
    Min,
    Window,
)
from django.db.models.functions import RowNumber

from catalog.models import (
    Item,
    PodcastEpisode,
    TVEpisode,
    item_content_types,
)
from common.models import SiteConfig
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity

from journal.models import (
    Collection,
    CollectionMember,
    Comment,
    Like,
    Mark,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfType,
    TagMember,
)

from .base import (
    BULK_DAY,
    CATEGORIES,
    _card_item,
    _category_of,
    _credits,
    _ctype_categories,
    _grades,
    _hidden_people,
    _items_by_id,
    _member,
    _members,
    _release_year,
    item_ref,
    plain_excerpt,
    year_range,
)
from .aggregates import (
    KINDRED_MIN_SHARED,
    _median,
    _percentile,
    instance_stats,
    kindred_matches,
)

logger = logging.getLogger(__name__)


FRIENDS_MAX = 200


COLLECTIONS_MAX = 30


COLLECTION_ITEMS_MAX = 10


# the Early card shows a place only up to this one (deck.js checks the same 10)
FIRST_RANK_MAX = 10


def _first_ranks(owner: APIdentity, ids: list[int]) -> dict[int, int]:
    """The member's place among the members who marked each title, for titles where it is
    FIRST_RANK_MAX or better. The count of earlier marks stops at FIRST_RANK_MAX rows, so a popular
    title costs no more than an obscure one."""
    if not ids:
        return {}
    sql = """
        SELECT a.item_id, (
            SELECT count(*) FROM (
                SELECT 1 FROM journal_shelfmember b
                WHERE b.item_id = a.item_id AND b.created_time < a.created_time
                LIMIT %s
            ) earlier
        )
        FROM journal_shelfmember a
        WHERE a.owner_id = %s AND a.item_id = ANY(%s)
    """
    with connection.cursor() as cur:
        cur.execute(sql, [FIRST_RANK_MAX, owner.pk, ids])
        rows = cur.fetchall()
    return {i: n + 1 for i, n in rows if n < FIRST_RANK_MAX}


def _done_before(
    owner: APIdentity,
    ids: list[int],
    when: dict[int, datetime.datetime],
    tz: ZoneInfo,
) -> dict[int, tuple[int, str]]:
    """Titles of the year that were also finished earlier, on another day: the number of those earlier days
    and the first of them. Each finish leaves a COMPLETE entry in the shelf log; a progress update leaves
    one too, with the progress in it, and that is not a finish. The entries take the visibility of the
    member's current mark, as the title does."""
    days: dict[int, set[datetime.date]] = defaultdict(set)
    for item_id, ts in (
        ShelfLogEntry.objects.filter(
            owner=owner, item_id__in=ids, shelf_type=ShelfType.COMPLETE
        )
        .exclude(metadata__has_key="progress_value")
        .values_list("item_id", "timestamp")
    ):
        days[item_id].add(ts.astimezone(tz).date())
    out = {}
    for item_id, ds in days.items():
        done = when[item_id].astimezone(tz).date()
        earlier = sorted(d for d in ds if d < done)
        if earlier:
            out[item_id] = (len(earlier), earlier[0].isoformat())
    return out


def _mark_vis(owner: APIdentity, item_ids: set[int]) -> dict[int, int]:
    """Visibility of the member's current mark of each title; the shelf log has none of its own."""
    return dict(
        _members()
        .filter(owner=owner, item_id__in=item_ids)
        .values_list("item_id", "visibility")
    )


def _activity(
    owner: APIdentity,
    level: int,
    start: datetime.datetime,
    end: datetime.datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    """Days with progress, completions, drops, notes, episode comments and reviews, at this level.

    A shelf log entry takes the visibility of the member's current mark of that title; an entry
    whose title is no longer on a shelf counts as private.
    """
    log = list(
        ShelfLogEntry.objects.filter(
            owner=owner,
            timestamp__gte=start,
            timestamp__lt=end,
            shelf_type__in=[ShelfType.PROGRESS, ShelfType.COMPLETE, ShelfType.DROPPED],
        ).values_list("item_id", "shelf_type", "timestamp")
    )
    vis = _mark_vis(owner, {i for i, _, _ in log})
    out = []
    for item_id, st, ts in log:
        if vis.get(item_id, 2) <= level:
            out.append({"date": ts.astimezone(tz).date().isoformat(), "kind": str(st)})
    # notes, comments on episodes (a comment on a whole title is part of its mark) and reviews
    ep_types = [item_content_types()[TVEpisode], item_content_types()[PodcastEpisode]]
    for model, kind, extra in (
        (Note, "note", {}),
        (Comment, "comment", {"item__polymorphic_ctype_id__in": ep_types}),
        (Review, "review", {}),
    ):
        for ts in model.objects.filter(
            owner=owner,
            created_time__gte=start,
            created_time__lt=end,
            visibility__lte=level,
            **extra,
        ).values_list("created_time", flat=True):
            out.append({"date": ts.astimezone(tz).date().isoformat(), "kind": kind})
    out.sort(key=lambda a: a["date"])
    return out


def _history(
    owner: APIdentity, level: int, year: int, tz: ZoneInfo, ctypes: dict[int, str]
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """Per year and month: titles on ordinary days (m), on bulk days (b), by shelf (c), days with
    a finished title (d), and rated (r), five-star (f) and grade sum (g) by the completion date."""
    rows = list(
        _members()
        .filter(
            owner=owner,
            parent__shelf_type=ShelfType.COMPLETE,
            visibility__lte=level,
            created_time__lt=datetime.datetime(year, 1, 1, tzinfo=tz),
        )
        .values_list("item_id", "created_time", "item__polymorphic_ctype_id")
    )
    grades = _grades(owner, [r[0] for r in rows], level)
    per_day: dict[datetime.date, int] = defaultdict(int)
    local = []
    for item_id, ts, ct in rows:
        d = ts.astimezone(tz).date()
        per_day[d] += 1
        local.append((item_id, d, _category_of(ct, ctypes)))
    years: dict[int, dict[str, Any]] = {}
    last_year: dict[str, int] = defaultdict(int)
    seen_days: set[datetime.date] = set()

    def z() -> list[int]:
        return [0] * 12

    first = None
    for item_id, d, cat in local:
        first = d if first is None or d < first else first
        y = years.setdefault(
            d.year,
            {"m": z(), "b": z(), "c": {}, "d": z(), "r": z(), "f": z(), "g": z()},
        )
        mo = d.month - 1
        if d.year == year - 1 and cat:
            last_year[cat] += 1
        # a bulk-import day counts toward nothing the cards compare, its ratings included
        if per_day[d] > BULK_DAY:
            y["b"][mo] += 1
            continue
        g = grades.get(item_id)
        if g:
            y["r"][mo] += 1
            y["g"][mo] += g
            if g == 10:
                y["f"][mo] += 1
        y["m"][mo] += 1
        if cat:
            y["c"].setdefault(cat, z())[mo] += 1
        if d not in seen_days:
            seen_days.add(d)
            y["d"][mo] += 1
    joined = owner.user.date_joined.astimezone(tz).date() if owner.user else None
    if first is None:
        return None, dict(last_year)
    return (
        {
            "joined": (joined or first).isoformat(),
            "first": first.isoformat(),
            "years": {str(k): v for k, v in sorted(years.items())},
        },
        dict(last_year),
    )


def _friends(
    owner: APIdentity,
    year: int,
    tz: ZoneInfo,
    my_items: dict[int, str],
    hidden: AbstractSet[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Followed members with public completions this year: their totals and the shared titles.

    Only their public marks; titles that are not also in this member's year are counted but not
    sent. A followed member who hides their own pages from signed-out visitors is still listed:
    that setting covers their homepage and their own cards, not being named on someone else's.
    """
    start, end = year_range(year, tz)
    # the cap applies to members with completions, so followed accounts with none never take their places
    following = [f for f in owner.following if f not in hidden]
    if not following:
        return []
    people = dict(
        APIdentity.objects.filter(pk__in=following, deleted__isnull=True).values_list(
            "pk", "username"
        )
    )
    if not people:
        return []
    qs = _members().filter(
        owner_id__in=list(people),
        parent__shelf_type=ShelfType.COMPLETE,
        visibility=0,
        created_time__gte=start,
        created_time__lt=end,
    )
    totals = dict(
        qs.values("owner_id").annotate(n=Count("pk")).values_list("owner_id", "n")
    )
    shared: dict[int, list[dict[str, str]]] = defaultdict(list)
    for owner_id, item_id, ts in qs.filter(item_id__in=list(my_items)).values_list(
        "owner_id", "item_id", "created_time"
    ):
        shared[owner_id].append(
            {"u": my_items[item_id], "d": ts.astimezone(tz).date().isoformat()}
        )
    # names and handles from the takahe identities in one query, not one per member
    ids = sorted((pk for pk in people if totals.get(pk)), key=lambda pk: -totals[pk])[
        :FRIENDS_MAX
    ]
    names = {
        pk: (name or username, f"{username}@{domain}")
        for pk, name, username, domain in TakaheIdentity.objects.filter(
            pk__in=ids
        ).values_list("pk", "name", "username", "domain_id")
    }
    # "id" lets each page leave out the members its visitor must not see; the page never sends it
    return [
        {
            "id": pk,
            "handle": names[pk][1] if pk in names else people[pk],
            "name": names[pk][0] if pk in names else people[pk],
            "n": totals[pk],
            "items": shared.get(pk, []),
        }
        for pk in ids
    ]


def _collections(
    owner: APIdentity,
    level: int,
    viewer: Any,
    hidden: AbstractSet[int],
    refs: list[str] | None,
    picker: bool,
    store: bool = False,
) -> list[dict[str, Any]]:
    """Collections a saved card uses, however old, and for the owner's picker their latest own collections at
    this level and the latest they liked. Another member's collection is that member's own content, so it
    must pass the journal's rule for this visitor, as its own page would: public, its author not hidden
    from the visitor (``hidden``), and for a signed-out visitor an author who allows them. ``store``: for
    the deck's stored data, which serves every visitor, so the visitor's rules are left to each page and
    each collection keeps its id and author for that."""
    others = Collection.objects.filter(visibility=0).exclude(owner=owner)
    if hidden:
        others = others.exclude(owner_id__in=hidden)
    if not store and (not viewer or not viewer.is_authenticated):
        others = others.filter(owner__anonymous_viewable=True)
    own, liked = [], []
    if picker:
        own = list(
            Collection.objects.filter(owner=owner, visibility__lte=level)
            .order_by("-edited_time")
            .values_list("pk", flat=True)[:COLLECTIONS_MAX]
        )
        liked = list(
            Like.objects.filter(owner=owner, target__in=others)
            .order_by("-created_time")
            .values_list("target_id", flat=True)[:COLLECTIONS_MAX]
        )
    uids = []
    for r in refs or []:
        try:
            uids.append(uuid.UUID(int=b62_decode(r)))
        except ValueError, IndexError:  # IndexError: b62_decode("")
            continue
    if uids:
        own += [
            pk
            for pk in Collection.objects.filter(
                owner=owner, visibility__lte=level, uid__in=uids
            ).values_list("pk", flat=True)
            if pk not in own
        ]
        liked += [
            pk
            for pk in others.filter(uid__in=uids).values_list("pk", flat=True)
            if pk not in liked
        ]
    ids = own + [i for i in liked if i not in own]
    if not ids:
        return []
    # the rows are read again under the same rules, so a collection made private meanwhile is left out
    own_rows = Collection.objects.filter(owner=owner, visibility__lte=level, pk__in=own)
    liked_rows = others.filter(pk__in=[i for i in liked if i not in own])
    cols = {
        c.pk: c
        for rows in (own_rows, liked_rows)
        for c in rows.select_related("owner").annotate(total=Count("members"))
    }
    members = (
        CollectionMember.objects.filter(parent_id__in=ids)
        .annotate(
            rn=Window(
                RowNumber(),
                partition_by=[F("parent_id")],
                order_by=[F("position").asc(), F("pk").asc()],
            )
        )
        .filter(rn__lte=COLLECTION_ITEMS_MAX)
        .values_list("parent_id", "item_id")
    )
    rows = list(members)
    items = _items_by_id(list({r[1] for r in rows}))
    by_col: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent_id, item_id in rows:
        it = items.get(item_id)
        if it is None or it.is_deleted:
            continue
        by_col[parent_id].append(item_ref(it))
    out = []
    for pk in ids:
        c = cols.get(pk)
        if c is None:
            continue
        out.append(
            {
                "uuid": c.uuid,
                "title": c.title,
                "rel": "own" if pk in own else "liked",
                "owner": c.owner.full_handle,
                "total": getattr(c, "total", 0),
                "items": by_col.get(pk, []),
                **({"author_id": c.owner_id} if store else {}),
            }
        )
    return out


def _reviews(
    owner: APIdentity,
    level: int,
    start: datetime.datetime,
    end: datetime.datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    out = []
    rows = list(
        Review.objects.filter(
            owner=owner,
            created_time__gte=start,
            created_time__lt=end,
            visibility__lte=level,
        ).values_list("item_id", "title", "body", "created_time")
    )
    items = _items_by_id(list({r[0] for r in rows}))
    for item_id, title, body, ts in rows:
        it = items.get(item_id)
        out.append(
            {
                "u": it.absolute_url if it else None,
                "title": title,
                "date": ts.astimezone(tz).date().isoformat(),
                "words": len((body or "").split()),
                "chars": len(body or ""),
                "excerpt": plain_excerpt(body),
            }
        )
    return out


def _waiting(
    owner: APIdentity, level: int, end: datetime.datetime, tz: ZoneInfo
) -> dict[str, Any] | None:
    """The title that has waited longest on the wishlist."""
    first = (
        _members()
        .filter(
            owner=owner,
            parent__shelf_type=ShelfType.WISHLIST,
            visibility__lte=level,
            created_time__lt=end,
            item__is_deleted=False,
        )
        .order_by("created_time")
        .values_list("item_id", "created_time")
        .first()
    )
    it = Item.objects.filter(pk=first[0]).first() if first else None
    if it is None or first is None:
        return None
    upto = min(end, datetime.datetime.now(tz=tz))
    return {**_card_item(it), "days": (upto - first[1]).days}


def _kindred(
    owner: APIdentity,
    year: int,
    matches: dict[int, list[list[float]]] | None,
    tz: ZoneInfo,
    my_items: dict[int, str],
    hidden: AbstractSet[int] = frozenset(),
) -> dict[str, Any] | None:
    """The kindred member for this deck: the best cached match the owner may be shown now, with the titles
    both finished this year in public. The batch skipped bulk days and used the site's time zone, so the
    shared titles are counted again here; a match now under the minimum gives way to the next.
    ``matches`` is the cached batch (None on a miss), read once by the caller so ``warming`` agrees."""
    candidates = [int(m[0]) for m in (matches or {}).get(owner.pk, [])]
    if not candidates or not my_items:
        return None
    # the whole following list
    skip = set(owner.following) | set(owner.ignoring) | set(hidden)
    candidates = [b for b in candidates if b not in skip]
    if not candidates:
        return None
    allowed = set(
        TakaheIdentity.objects.filter(pk__in=candidates, discoverable=True).values_list(
            "pk", flat=True
        )
    )
    excluded = SiteConfig.system.discover_exclude_posts_from
    if excluded and allowed:
        allowed -= Takahe.get_identity_ids_by_handles(excluded)
    people = {
        p.pk: p
        for p in APIdentity.objects.filter(pk__in=list(allowed), deleted__isnull=True)
    }
    start, end = year_range(year, tz)
    for b in candidates:
        person = people.get(b)
        if person is None:
            continue
        shared: dict[int, datetime.datetime] = {}
        for item_id, ts in (
            _members()
            .filter(
                owner_id=b,
                parent__shelf_type=ShelfType.COMPLETE,
                visibility=0,
                created_time__gte=start,
                created_time__lt=end,
                item_id__in=list(my_items),
            )
            .order_by("created_time")
            .values_list("item_id", "created_time")
        ):
            shared.setdefault(item_id, ts)
        if len(shared) < KINDRED_MIN_SHARED:
            continue
        return {
            "id": person.pk,
            "handle": person.full_handle,
            "name": person.display_name,
            "url": person.url,
            "n": len(shared),
            "items": [
                {"u": my_items[i], "d": ts.astimezone(tz).date().isoformat()}
                for i, ts in shared.items()
            ],
        }
    return None


def _year_titles(
    owner: APIdentity, level: int, start: datetime.datetime, end: datetime.datetime
) -> tuple[list[int], dict[int, Item], dict[int, datetime.datetime]]:
    """The titles finished in the year at this level, in order: their ids, the items, and when each was finished."""
    marks = list(
        _members()
        .filter(
            owner=owner,
            parent__shelf_type=ShelfType.COMPLETE,
            visibility__lte=level,
            created_time__gte=start,
            created_time__lt=end,
        )
        .order_by("created_time")
        .values_list("item_id", "created_time")
    )
    ids = [m[0] for m in marks]
    items = _items_by_id(ids)
    ids = [
        i
        for i in ids
        if i in items
        and not items[i].is_deleted
        and str(items[i].category) in CATEGORIES
    ]
    return ids, items, dict(marks)


def live_year(owner: APIdentity, year: int, level: int, tz: ZoneInfo) -> dict[str, Any]:
    """The year's finished titles and active days at this level, read at each view: the page's whole-year
    section (the counts and the month piles) stays current, while the cards show what the last update
    stored. Before the deck is made, this is all the page shows."""
    start, end = year_range(year, tz)
    ids, items, when = _year_titles(owner, level, start, end)
    rating = _grades(owner, ids, level)
    return {
        "items": [
            {
                "uuid": items[i].uuid,
                "title": items[i].display_title,
                "category": str(items[i].category),
                "cover": items[i].display_cover_image_url,
                "url": items[i].absolute_url,
                "rating": rating.get(i),
                "date": when[i].astimezone(tz).date().isoformat(),
            }
            for i in ids
        ],
        "activity": _activity(owner, level, start, end, tz),
    }


def build_payload(
    owner: APIdentity,
    year: int,
    level: int,
    tz: ZoneInfo,
    include_friends: bool = True,
    include_kindred: bool = False,
    viewer: Any = None,
    collection_refs: list[str] | None = None,
    picker: bool = True,
    hidden: AbstractSet[int] | None = None,
    instance: tuple[Any, Any] | None = None,
) -> dict[str, Any]:
    """``hidden``: the members to leave out of the social cards; by default those ``viewer`` must not see.
    The stored data passes none, as it serves every visitor and each page applies its own visitor.
    ``instance``: the year's instance totals and kindred matches, when the caller already read them."""
    start, end = year_range(year, tz)
    ctypes = _ctype_categories()
    ids, items, when = _year_titles(owner, level, start, end)
    rating = _grades(owner, ids, level)
    # plain text with spoilers taken out, as the cards draw comments verbatim
    comment = {
        item_id: plain_excerpt(text, 2000)
        for item_id, text in Comment.objects.filter(
            owner=owner, item_id__in=ids, visibility__lte=level
        ).values_list("item_id", "text")
    }
    tags: dict[int, list[str]] = defaultdict(list)
    for item_id, title in TagMember.objects.filter(
        owner=owner,
        item_id__in=ids,
        visibility__lte=level,
        parent__visibility__lte=level,
    ).values_list("item_id", "parent__title"):
        tags[item_id].append(title)
    wished = dict(
        ShelfLogEntry.objects.filter(
            owner=owner, item_id__in=ids, shelf_type=ShelfType.WISHLIST
        )
        .values("item_id")
        .annotate(t=Min("timestamp"))
        .values_list("item_id", "t")
    )
    item_list = [items[i] for i in ids]
    info = Rating.get_info_for_items(item_list)
    counts = Mark.get_mark_count_for_items(item_list)
    credits = _credits(ids)
    ranks = _first_ranks(owner, ids)
    again = _done_before(owner, ids, when, tz)
    out_items = []
    paths: dict[int, str] = {}
    for n, i in enumerate(ids):
        it = items[i]
        ts = when[i].astimezone(tz)
        w = wished.get(i)
        paths[i] = it.url
        out_items.append(
            {
                "uuid": it.uuid,
                "title": it.display_title,
                "category": str(it.category),
                "cover": it.display_cover_image_url,
                "url": it.absolute_url,
                "rating": rating.get(i),
                "date": ts.date().isoformat(),
                "comment": comment.get(i),
                "site_rating": info.get(i, {}).get("average"),
                "dist": info.get(i, {}).get("distribution"),
                "raters": info.get(i, {}).get("count") or 0,
                "marks": counts.get(i, 0),
                "year": _release_year(it),
                "credits": credits.get(i, []),
                "wishlisted": w.astimezone(tz).date().isoformat()
                if w and w < when[i]
                else None,
                "first_rank": ranks.get(i),
                "tags": tags.get(i, []),
                # finished before, on other days: how many times, and the first of them
                "done_before": again[i][0] if i in again else 0,
                "first_done": again[i][1] if i in again else None,
            }
        )
    history, last_year = _history(owner, level, year, tz, ctypes)
    waiting = _waiting(owner, level, end, tz)
    stats, matches = instance or (instance_stats(year), None)
    if not include_kindred:
        matches = None
    elif instance is None:
        matches = kindred_matches(year)
    # other members appear on the cards only as this visitor may see them
    if hidden is None:
        hidden = _hidden_people(viewer)
    payload: dict[str, Any] = {
        "year": year,
        "member": _member(owner),
        "items": out_items,
        "activity": _activity(owner, level, start, end, tz),
        "history": history,
        "last_year": last_year or None,
        "waiting": waiting,
        "collections": _collections(
            owner, level, viewer, hidden, collection_refs, picker
        ),
        "reviews": _reviews(owner, level, start, end, tz),
        "friends": _friends(owner, year, tz, paths, hidden) if include_friends else [],
        "kindred": _kindred(owner, year, matches, tz, paths, hidden)
        if include_kindred
        else None,
        "instance_top": stats["top"] if stats else None,
        "percentile": _percentile(stats["counts"][level], len(ids)) if stats else None,
        "median": _median(stats["counts"][level]) if stats else None,
        # a job's result is not cached yet: the cards that need it are missing for now, so the owner's
        # page must not save that list as the one visitors see
        "warming": stats is None or (include_kindred and matches is None),
    }
    return payload
