"""Data for the annual summary deck.

``build_payload`` reads one member's year at one visibility level (0 public, 1 followers only,
2 only the member) and returns the dict the deck script renders from. Only pieces at or below
that level go in, so a public page never carries a followers-only comment or a private day.
Every query is batched: the number of queries does not grow with the number of titles.
"""

import datetime
import hashlib
import logging
import math
import re
import statistics
import uuid
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

import django_rq
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, F, Min, QuerySet, Window
from django.db.models.functions import RowNumber, TruncDate
from django.urls import reverse

from catalog.models import (
    Item,
    ItemCredit,
    PodcastEpisode,
    TVEpisode,
    item_content_types,
)
from common.models import SiteConfig
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity

from .models import (
    Collection,
    CollectionMember,
    Comment,
    Like,
    Mark,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfType,
    TagMember,
    WrappedDeck,
)
from .models.wrapped import ALL_HIDDEN, AUDIENCES

logger = logging.getLogger(__name__)

CATEGORIES = ["book", "movie", "tv", "music", "game", "podcast", "performance"]
BULK_DAY = 30
FRIENDS_MAX = 200
COLLECTIONS_MAX = 30
COLLECTION_ITEMS_MAX = 10
EXCERPT_CHARS = 200
# the "your words" card: one text of the member's on one title, up to this many characters, then cut
WORDS_CHARS = 600
WORDS_KINDS = ("comment", "note", "review")
INSTANCE_CACHE_SECONDS = 24 * 3600
SOCIAL_CARDS = {"company", "twin", "first"}
# the kindred card: a member the owner does not follow whose public year overlaps the owner's the most
KINDRED_MIN_SHARED = 5
# titles more members finished than this are left out: they say little about taste and cost the most
# (2026 on neodb.social: 6 of 105,248 titles, by a read-only count on 2026-11-27)
KINDRED_ITEM_CAP = 300
KINDRED_KEEP = 10
# the job refreshes the matches daily; the cache outlives that, so a page never waits for the batch
KINDRED_CACHE_SECONDS = 3 * 24 * 3600
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


def _int_attr(item: Item, attr: str) -> int | None:
    try:
        v = getattr(item, attr, None)
        return int(v) if v else None
    except TypeError, ValueError:
        return None


def plain_excerpt(md: str, size: int = EXCERPT_CHARS) -> str:
    """A short plain-text start of a review: no spoilers, images or markdown marks."""
    s = re.sub(r">!.*?!<", " ", md or "", flags=re.S).split(">!")[0]
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[#*_>`~]+", "", s)
    return re.sub(r"\s+", " ", s).strip()[:size]


def _member(identity: APIdentity) -> dict[str, Any]:
    ti = identity.takahe_identity if identity.local else None
    icon = ti.icon if ti else None
    return {
        "name": identity.display_name,
        "handle": "@" + identity.full_handle,
        "avatar": identity.avatar,
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
    # the polymorphic manager returns subclasses in one query per subclass, so jsondata fields resolve
    return {i.pk: i for i in Item.objects.filter(pk__in=ids)}


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


def _first_ranks(owner: APIdentity, ids: list[int]) -> dict[int, int]:
    """For each title, how many members marked it before this member did, plus one."""
    if not ids:
        return {}
    sql = """
        SELECT a.item_id, count(b.piece_ptr_id)
        FROM journal_shelfmember a
        JOIN journal_shelfmember b ON b.item_id = a.item_id AND b.created_time < a.created_time
        WHERE a.owner_id = %s AND a.item_id = ANY(%s)
        GROUP BY a.item_id
    """
    with connection.cursor() as cur:
        cur.execute(sql, [owner.pk, ids])
        earlier = dict(cur.fetchall())
    return {i: earlier.get(i, 0) + 1 for i in ids}


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
    """Days with progress, completions, drops, notes, episode comments and reviews.

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
        v = vis.get(item_id, 2)
        if v <= level:
            out.append(
                {
                    "date": ts.astimezone(tz).date().isoformat(),
                    "kind": str(st),
                    "vis": v,
                }
            )
    for ts, v in Note.objects.filter(
        owner=owner,
        created_time__gte=start,
        created_time__lt=end,
        visibility__lte=level,
    ).values_list("created_time", "visibility"):
        out.append(
            {"date": ts.astimezone(tz).date().isoformat(), "kind": "note", "vis": v}
        )
    ep_types = [item_content_types()[TVEpisode], item_content_types()[PodcastEpisode]]
    for ts, v in Comment.objects.filter(
        owner=owner,
        created_time__gte=start,
        created_time__lt=end,
        visibility__lte=level,
        item__polymorphic_ctype_id__in=ep_types,
    ).values_list("created_time", "visibility"):
        out.append(
            {"date": ts.astimezone(tz).date().isoformat(), "kind": "comment", "vis": v}
        )
    for ts, v in Review.objects.filter(
        owner=owner,
        created_time__gte=start,
        created_time__lt=end,
        visibility__lte=level,
    ).values_list("created_time", "visibility"):
        out.append(
            {"date": ts.astimezone(tz).date().isoformat(), "kind": "review", "vis": v}
        )
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
    grades = dict(
        Rating.objects.filter(
            owner=owner,
            visibility__lte=level,
            grade__isnull=False,
            item_id__in=[r[0] for r in rows],
        ).values_list("item_id", "grade")
    )
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
        g = grades.get(item_id)
        if g:
            y["r"][mo] += 1
            y["g"][mo] += g
            if g == 10:
                y["f"][mo] += 1
        if d.year == year - 1 and cat:
            last_year[cat] += 1
        if per_day[d] > BULK_DAY:
            y["b"][mo] += 1
            continue
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
            "handle": owner.full_handle,
            "joined": (joined or first).isoformat(),
            "first": first.isoformat(),
            "years": {str(k): v for k, v in sorted(years.items())},
        },
        dict(last_year),
    )


def _friends(
    owner: APIdentity, year: int, tz: ZoneInfo, my_items: dict[int, str]
) -> list[dict[str, Any]]:
    """Followed members with public completions this year: their totals and the shared titles.

    Only their public marks; titles that are not also in this member's year are counted but not
    sent. A followed member who hides their own pages from signed-out visitors is still listed:
    that setting covers their homepage and their own cards, not being named on someone else's.
    """
    start, end = year_range(year, tz)
    following = list(owner.following)[:FRIENDS_MAX]
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
    ids = [pk for pk in people if totals.get(pk)]
    names = {
        pk: (name or username, f"{username}@{domain}")
        for pk, name, username, domain in TakaheIdentity.objects.filter(
            pk__in=ids
        ).values_list("pk", "name", "username", "domain_id")
    }
    return [
        {
            "handle": names[pk][1] if pk in names else people[pk],
            "name": names[pk][0] if pk in names else people[pk],
            "n": totals[pk],
            "items": shared.get(pk, []),
        }
        for pk in ids
    ]


def _collections(owner: APIdentity, level: int) -> list[dict[str, Any]]:
    own = list(
        Collection.objects.filter(owner=owner, visibility__lte=level)
        .order_by("-edited_time")
        .values_list("pk", flat=True)[:COLLECTIONS_MAX]
    )
    liked = list(
        Like.objects.filter(
            owner=owner, target__in=Collection.objects.filter(visibility=0)
        )
        .order_by("-created_time")
        .values_list("target_id", flat=True)[:COLLECTIONS_MAX]
    )
    ids = own + [i for i in liked if i not in own]
    if not ids:
        return []
    cols = {
        c.pk: c
        for c in Collection.objects.filter(pk__in=ids)
        .select_related("owner")
        .annotate(total=Count("members"))
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
        .values_list("parent_id", "item_id", "metadata")
    )
    rows = list(members)
    items = _items_by_id(list({r[1] for r in rows}))
    by_col: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent_id, item_id, meta in rows:
        it = items.get(item_id)
        if it is None or it.is_deleted:
            continue
        by_col[parent_id].append(
            {
                "u": it.url,
                "t": it.display_title,
                "cov": it.display_cover_image_url,
                "c": str(it.category),
                "n": (meta or {}).get("note") or None,
            }
        )
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
        ).values_list("item_id", "title", "body", "created_time", "visibility")
    )
    items = _items_by_id(list({r[0] for r in rows}))
    for item_id, title, body, ts, vis in rows:
        it = items.get(item_id)
        out.append(
            {
                "u": it.absolute_url if it else None,
                "title": title,
                "date": ts.astimezone(tz).date().isoformat(),
                "words": len((body or "").split()),
                "chars": len(body or ""),
                "excerpt": plain_excerpt(body),
                "vis": vis,
            }
        )
    return out


def _waiting(
    owner: APIdentity, level: int, end: datetime.datetime, tz: ZoneInfo
) -> tuple[dict[str, Any] | None, int]:
    """The title that has waited longest on the wishlist, and how many wait there."""
    qs = _members().filter(
        owner=owner,
        parent__shelf_type=ShelfType.WISHLIST,
        visibility__lte=level,
        created_time__lt=end,
    )
    pending = qs.count()
    first = qs.order_by("created_time").values_list("item_id", "created_time").first()
    if not first:
        return None, pending
    it = Item.objects.filter(pk=first[0]).first()
    if it is None:
        return None, pending
    upto = min(end, datetime.datetime.now(tz=tz))
    return (
        {
            "title": it.display_title,
            "url": it.absolute_url,
            "cover": it.display_cover_image_url,
            "days": (upto - first[1]).days,
            "category": str(it.category),
        },
        pending,
    )


def _instance_key(year: int) -> str:
    return f"wrapped:instance:{year}"


def compute_instance_stats(year: int) -> dict[str, Any]:
    """The instance's most finished title of the year, and every local member's completion count.

    A whole-instance aggregate; it runs in a job and the result is cached, never per request.
    """
    tz = ZoneInfo(settings.TIME_ZONE)
    start, end = year_range(year, tz)
    qs = _members().filter(
        parent__shelf_type=ShelfType.COMPLETE,
        created_time__gte=start,
        created_time__lt=end,
        owner__local=True,
        owner__deleted__isnull=True,
    )
    top = qs.values("item_id").annotate(n=Count("pk")).order_by("-n", "item_id").first()
    counts = sorted(
        qs.values("owner_id").annotate(n=Count("pk")).values_list("n", flat=True)
    )
    top_item = None
    if top:
        it = Item.objects.filter(pk=top["item_id"]).first()
        if it:
            top_item = {
                "title": it.display_title,
                "url": it.absolute_url,
                "cover": it.display_cover_image_url,
                "category": str(it.category),
                "count": top["n"],
            }
    stats = {"top": top_item, "counts": counts}
    cache.set(_instance_key(year), stats, INSTANCE_CACHE_SECONDS)
    return stats


def instance_stats(year: int) -> dict[str, Any] | None:
    """Cached instance stats; on a miss, queue the job and return None so the page stays fast."""
    stats = cache.get(_instance_key(year))
    if stats is None and cache.add(_instance_key(year) + ":lock", 1, 600):
        try:
            django_rq.get_queue("cron").enqueue(compute_instance_stats, year)
        except Exception as e:
            logger.warning(f"unable to queue wrapped instance stats: {e}")
    return stats


def _percentile(counts: list[int], mine: int) -> int | None:
    if len(counts) < 10 or not mine:
        return None
    below = sum(1 for c in counts if c < mine)
    return round(below * 100 / len(counts))


def _median(counts: list[int]) -> int | None:
    """The titles one real member finished (the lower middle one), with the same minimum as the percentile."""
    if len(counts) < 10:
        return None
    return statistics.median_low(counts)


def _kindred_key(year: int) -> str:
    return f"wrapped:kindred:{year}"


def compute_kindred(year: int) -> dict[int, list[list[float]]]:
    """For each local member, up to KINDRED_KEEP members who could be their kindred member this year,
    best first, as [member id, score, shared titles].

    A whole-instance batch for the job; the result is cached and a page only reads it. Public completions
    only, without bulk-import days (more than BULK_DAY in one day). A candidate is any member, local or
    remote, who is discoverable, not deleted and not on the site's exclude list; whether the member allows
    signed-out visitors does not matter, as that covers only their own pages. Each title weighs
    log((members + 1) / finishers), so a shared rare title counts for more, and titles with more than
    KINDRED_ITEM_CAP finishers are left out; the score is the shared weight divided by the square root of
    the two members' weights, and at least KINDRED_MIN_SHARED titles must be shared. Who the owner
    follows, blocks or mutes changes often, so the page checks that when it is built.
    """
    from catalog.recommendation import excluded_identity_ids  # catalog imports journal

    tz = ZoneInfo(settings.TIME_ZONE)
    start, end = year_range(year, tz)
    qs = _members().filter(
        parent__shelf_type=ShelfType.COMPLETE,
        visibility=0,
        created_time__gte=start,
        created_time__lt=end,
        owner__deleted__isnull=True,
    )
    bulk = set(
        qs.annotate(day=TruncDate("created_time", tzinfo=tz))
        .values("owner_id", "day")
        .annotate(n=Count("pk"))
        .filter(n__gt=BULK_DAY)
        .values_list("owner_id", "day")
    )
    titles: dict[int, set[int]] = defaultdict(set)
    for owner_id, item_id, ts in qs.values_list(
        "owner_id", "item_id", "created_time"
    ).iterator(chunk_size=5000):
        if bulk and (owner_id, ts.astimezone(tz).date()) in bulk:
            continue
        titles[owner_id].add(item_id)
    finishers: dict[int, list[int]] = defaultdict(list)
    for owner_id, items in titles.items():
        for item_id in items:
            finishers[item_id].append(owner_id)
    members = len(titles)
    weight = {
        i: math.log((members + 1) / len(f))
        for i, f in finishers.items()
        if 2 <= len(f) <= KINDRED_ITEM_CAP
    }
    total = {o: sum(weight.get(i, 0.0) for i in items) for o, items in titles.items()}
    excluded = excluded_identity_ids()
    local = set(
        APIdentity.objects.filter(pk__in=list(titles), local=True).values_list(
            "pk", flat=True
        )
    )
    result: dict[int, list[list[float]]] = {}
    for a in local:
        mine = [i for i in titles[a] if i in weight]
        if len(mine) < KINDRED_MIN_SHARED:
            continue
        acc: dict[int, list[float]] = {}
        for i in mine:
            w = weight[i]
            for b in finishers[i]:
                if b == a or b in excluded:
                    continue
                e = acc.get(b)
                if e is None:
                    acc[b] = [w, 1]
                else:
                    e[0] += w
                    e[1] += 1
        best = sorted(
            (
                (sw / math.sqrt(total[a] * total[b]), b, int(n))
                for b, (sw, n) in acc.items()
                if n >= KINDRED_MIN_SHARED
            ),
            reverse=True,
        )[:KINDRED_KEEP]
        if best:
            result[a] = [[b, round(score, 4), n] for score, b, n in best]
    cache.set(_kindred_key(year), result, KINDRED_CACHE_SECONDS)
    return result


def kindred_matches(year: int) -> dict[int, list[list[float]]] | None:
    """Cached kindred matches; on a miss, queue the job and return None so the page stays fast."""
    matches = cache.get(_kindred_key(year))
    if matches is None and cache.add(_kindred_key(year) + ":lock", 1, 600):
        try:
            django_rq.get_queue("cron").enqueue(compute_kindred, year)
        except Exception as e:
            logger.warning(f"unable to queue wrapped kindred matches: {e}")
    return matches


def _kindred(
    owner: APIdentity, year: int, tz: ZoneInfo, my_items: dict[int, str]
) -> dict[str, Any] | None:
    """The kindred member for this deck: the best cached match the owner may be shown now, with the titles
    both finished this year in public. The batch skipped bulk days and used the site's time zone, so the
    shared titles are counted again here; a match now under the minimum gives way to the next."""
    candidates = [int(m[0]) for m in (kindred_matches(year) or {}).get(owner.pk, [])]
    if not candidates or not my_items:
        return None
    # the whole following list, not the capped one the friend cards use
    skip = (
        set(Takahe.get_following_ids(owner.pk))
        | set(Takahe.get_rejecting_ids(owner.pk))
        | set(Takahe.get_muting_ids(owner.pk))
    )
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
        ident = (
            TakaheIdentity.objects.filter(pk=b)
            .values_list("name", "username", "domain_id")
            .first()
        )
        name, username, domain = ident if ident else ("", person.username, None)
        return {
            "handle": f"{username}@{domain}" if domain else person.handle,
            "name": name or username,
            "url": person.url,
            "n": len(shared),
            "items": [
                {"u": my_items[i], "d": ts.astimezone(tz).date().isoformat()}
                for i, ts in shared.items()
            ],
        }
    return None


def build_payload(
    owner: APIdentity,
    year: int,
    level: int,
    tz: ZoneInfo,
    include_friends: bool = True,
    include_kindred: bool = False,
) -> dict[str, Any]:
    start, end = year_range(year, tz)
    ctypes = _ctype_categories()
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
    rating = dict(
        Rating.objects.filter(
            owner=owner, item_id__in=ids, visibility__lte=level, grade__isnull=False
        ).values_list("item_id", "grade")
    )
    comment = dict(
        Comment.objects.filter(
            owner=owner, item_id__in=ids, visibility__lte=level
        ).values_list("item_id", "text")
    )
    tags: dict[int, list[list[Any]]] = defaultdict(list)
    for item_id, title, vis, tvis in TagMember.objects.filter(
        owner=owner,
        item_id__in=ids,
        visibility__lte=level,
        parent__visibility__lte=level,
    ).values_list("item_id", "parent__title", "visibility", "parent__visibility"):
        tags[item_id].append([title, max(vis, tvis)])
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
    when = dict(marks)
    out_items = []
    paths: dict[int, str] = {}
    for n, i in enumerate(ids):
        it = items[i]
        ts = when[i].astimezone(tz)
        w = wished.get(i)
        paths[i] = it.url
        out_items.append(
            {
                "id": n + 1,
                "uuid": it.uuid,
                "title": it.display_title,
                "category": str(it.category),
                "cover": it.display_cover_image_url,
                "url": it.absolute_url,
                "rating": rating.get(i),
                "date": ts.date().isoformat(),
                "time": ts.strftime("%H:%M"),
                "comment": comment.get(i),
                "site_rating": info.get(i, {}).get("average"),
                "dist": info.get(i, {}).get("distribution"),
                "marks": counts.get(i, 0),
                "year": _release_year(it),
                "pages": _int_attr(it, "pages"),
                "length": _int_attr(it, "length"),
                "genre": [str(g) for g in (getattr(it, "genre", None) or [])][:5],
                "credits": credits.get(i, []),
                "wishlisted": w.astimezone(tz).date().isoformat()
                if w and w < when[i]
                else None,
                "first_rank": ranks.get(i),
                "tags": tags.get(i, []),
            }
        )
    history, last_year = _history(owner, level, year, tz, ctypes)
    waiting, pending = _waiting(owner, level, end, tz)
    stats = instance_stats(year)
    payload: dict[str, Any] = {
        "year": year,
        "member": _member(owner),
        "items": out_items,
        "activity": _activity(owner, level, start, end, tz),
        "history": history,
        "last_year": last_year or None,
        "waiting": waiting,
        "wishlist_pending": pending,
        "collections": _collections(owner, level),
        "reviews": _reviews(owner, level, start, end, tz),
        "friends": _friends(owner, year, tz, {i: paths[i] for i in ids})
        if include_friends
        else [],
        "kindred": _kindred(owner, year, tz, {i: paths[i] for i in ids})
        if include_kindred
        else None,
        "instance_top": stats["top"] if stats else None,
        "percentile": _percentile(stats["counts"], len(ids)) if stats else None,
        "median": _median(stats["counts"]) if stats else None,
    }
    return payload


def _words_model(kind: str):
    return {"comment": Comment, "note": Note, "review": Review}[kind]


def words_text(kind: str, piece: Any) -> dict[str, Any]:
    """The plain text of a comment, note or review for the card, spoilers taken out, and whether it was cut."""
    if kind == "comment":
        raw, title, progress = piece.text, None, ""
    elif kind == "note":
        raw, title, progress = piece.content, piece.title, piece.progress_display
    else:
        raw, title, progress = piece.body, piece.title, ""
    plain = plain_excerpt(raw, WORDS_CHARS + 1)
    return {
        "text": plain[:WORDS_CHARS],
        "more": len(plain) > WORDS_CHARS,
        "title": title or None,
        "progress": progress or "",
        "chars": len(raw or ""),
    }


def _words_item(it: Item, rating: int | None) -> dict[str, Any]:
    return {
        "url": it.absolute_url,
        "title": it.display_title,
        "cover": it.display_cover_image_url,
        "category": str(it.category),
        "rating": rating,
        "credits": _credits([it.pk]).get(it.pk, []),
    }


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


def resolve_words(
    owner: APIdentity, made: list[dict[str, Any]], level: int, tz: ZoneInfo
) -> dict[str, dict[str, Any]]:
    """For each made "words" card, by card id, the text it shows at this level.

    A card whose text is gone, or is more private than the level, is not in the result, so its text
    never leaves the server for that audience.
    """
    refs: dict[str, dict[str, str]] = defaultdict(dict)
    for m in made:
        src = m.get("source") or {}
        if (
            src.get("type") == "words"
            and src.get("kind") in WORDS_KINDS
            and _is_uuid(src.get("ref"))
        ):
            refs[src["kind"]][str(uuid.UUID(src["ref"]))] = str(m.get("id"))
    found: list[tuple[str, str, Any]] = []
    for kind, cards in refs.items():
        for piece in _words_model(kind).objects.filter(
            owner=owner, uid__in=list(cards), visibility__lte=level
        ):
            found.append((cards[str(piece.uid)], kind, piece))
    items = _items_by_id(list({p.item_id for _, _, p in found}))
    grades = dict(
        Rating.objects.filter(
            owner=owner,
            item_id__in=list(items),
            visibility__lte=level,
            grade__isnull=False,
        ).values_list("item_id", "grade")
    )
    out = {}
    for card_id, kind, piece in found:
        it = items.get(piece.item_id)
        if not it or it.is_deleted:
            continue
        out[card_id] = {
            "kind": kind,
            **words_text(kind, piece),
            "date": piece.created_time.astimezone(tz).date().isoformat(),
            "item": _words_item(it, grades.get(it.pk)),
        }
    return out


def words_menu(
    owner: APIdentity, year: int, tz: ZoneInfo, limit: int = 400
) -> list[dict[str, Any]]:
    """Every title the owner wrote about in the year, with the comments, notes and reviews, newest first."""
    start, end = year_range(year, tz)
    rows: list[tuple[str, Any]] = []
    for kind in WORDS_KINDS:
        rows += [
            (kind, p)
            for p in _words_model(kind)
            .objects.filter(owner=owner, created_time__gte=start, created_time__lt=end)
            .order_by("-created_time")[:limit]
        ]
    rows = sorted(rows, key=lambda r: r[1].created_time, reverse=True)[:limit]
    items = _items_by_id(list({p.item_id for _, p in rows}))
    grades = dict(
        Rating.objects.filter(
            owner=owner, item_id__in=list(items), grade__isnull=False
        ).values_list("item_id", "grade")
    )
    works: dict[int, dict[str, Any]] = {}
    for kind, p in rows:
        it = items.get(p.item_id)
        if not it or it.is_deleted or str(it.category) not in CATEGORIES:
            continue
        w = works.setdefault(it.pk, {**_words_item(it, grades.get(it.pk)), "texts": []})
        w["texts"].append(
            {
                "kind": kind,
                "ref": str(p.uid),
                "vis": int(p.visibility),
                "date": p.created_time.astimezone(tz).date().isoformat(),
                "sensitive": bool(getattr(p, "sensitive", False)),
                **words_text(kind, p),
            }
        )
    return list(works.values())


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


def friends_allowed(deck: WrappedDeck | None, level: int, is_owner: bool) -> bool:
    """A visitor gets followed members' data only when a social card can be shown to their audience."""
    if is_owner:
        return True
    if not deck:
        return False
    shown = deck.shown_for(level)
    if shown is not None:
        return bool(SOCIAL_CARDS.intersection(shown))
    return any(deck.card_hidden(level, c) is not True for c in SOCIAL_CARDS)


def kindred_allowed(deck: WrappedDeck | None, level: int, is_owner: bool) -> bool:
    """The kindred card names a member the owner does not follow, so by default only the owner sees it;
    another audience gets its data only when the owner turned the card on for that audience."""
    if is_owner:
        return True
    if not deck:
        return False
    shown = deck.shown_for(level)
    if shown is not None:
        return "kindred" in shown
    return deck.card_hidden(level, "kindred") is False
