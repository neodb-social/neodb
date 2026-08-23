"""Drag-to-sort registration captcha.

A visitor who has verified an identity but not yet picked a username is shown
a set of item covers and two category rows, and must drag each cover into the
row it belongs to. Passing needs both a correct sort and interaction telemetry
consistent with a person moving a pointer.

Deliberate constraints, each load-bearing:

- Only opaque per-challenge tokens reach the client. Item pks, uuids, titles
  and category names stay server side, so a tile cannot be resolved back to a
  catalog entry from the page.
- Covers carry no title text. A title is a search key: the public catalog
  search returns an item's category for any title, so showing one would hand
  the answer over.
- The whole step shares one time budget and a fixed number of regenerations;
  see ``CAPTCHA_TTL`` and ``CAPTCHA_MAX_REGENERATIONS``.

The module mirrors ``users.login_proof``: tunables live at module level so
tests can relax them, and state lives in the session rather than in a signed
client payload.
"""

import hashlib
import io
import json
import random
import secrets
from itertools import pairwise
from typing import Any, TypedDict, cast

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.http import HttpRequest
from django.utils import timezone
from loguru import logger
from PIL import Image

from catalog.models import (
    AvailableItemCategory,
    Item,
    ItemCategory,
    PerformanceProduction,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    item_categories,
    item_content_types,
)
from common.models import SiteConfig
from journal.models import ShelfMember, q_item_in_category

CAPTCHA_TTL = 5 * 60
CAPTCHA_MAX_REGENERATIONS = 2
CAPTCHA_POOL_LIMIT = 1000
CAPTCHA_POOL_TTL = 48 * 3600
CAPTCHA_FAIL_OPEN_TTL = 60
# how many candidates to load per tile needed, so stale pool entries can be
# replaced without another round trip
CAPTCHA_DRAW_OVERSAMPLE = 3
# every tile is the same square and the same mime type; see render_tile
CAPTCHA_TILE_PX = 240
CAPTCHA_TILE_TTL = 600
CAPTCHA_TILE_CONTENT_TYPE = "image/jpeg"
# how long a solved pass stays good; the session cookie itself lives for months
CAPTCHA_PASS_TTL = 30 * 60
# per-challenge jitter, so a tile is not a checksum of a public cover
CAPTCHA_TILE_JITTER_PX = 3
CAPTCHA_TILE_QUALITY_MIN = 80
CAPTCHA_TILE_QUALITY_MAX = 90

SESSION_KEY = "registration_captcha"
PASSED_KEY = "registration_captcha_passed"
# one-shot: set when a quiz is solved, consumed by the page that celebrates it
CELEBRATE_KEY = "registration_captcha_solved"

_POPULAR_CACHE_PREFIX = "captcha_pool_popular"
_ALL_CACHE_PREFIX = "captcha_pool_all"
_TILE_CACHE_PREFIX = "captcha_tile"
_NONCE_CACHE_PREFIX = "captcha_nonce_used"
_FAIL_OPEN_CACHE_KEY = "captcha_fail_open"
_WARN_CACHE_KEY = "captcha_fail_open_warned"

# Sub-item classes: covers are generic and titles are unrecognizable out of
# context, so sorting them by cover alone would be unfair. PerformanceProduction
# is moot while Performance is excluded wholesale, and kept for clarity.
EXCLUDED_CAPTCHA_CLASSES: tuple[type[Item], ...] = (
    TVSeason,
    TVEpisode,
    PerformanceProduction,
    PodcastEpisode,
)

# Performances are excluded: too few instances carry cover art distinct enough
# to sort against another category.
EXCLUDED_CAPTCHA_CATEGORIES = frozenset({ItemCategory.Performance})


class Tile(TypedDict):
    pk: int
    row: int


class Challenge(TypedDict):
    started: int
    nonce: str
    regens: int
    rows: list[str]
    tiles: dict[str, Tile]
    msg: str | None


def is_enabled() -> bool:
    return SiteConfig.system.registration_captcha_items > 0


def _now() -> int:
    return int(timezone.now().timestamp())


def eligible_categories() -> list[ItemCategory]:
    """Visible catalog categories the captcha may quiz on."""
    available = set(AvailableItemCategory.values)
    hidden = set(SiteConfig.system.hidden_categories)
    return [
        c
        for c in item_categories()
        if c.value in available
        and c.value not in hidden
        and c not in EXCLUDED_CAPTCHA_CATEGORIES
    ]


def _category_ctype_ids(category: ItemCategory) -> list[int]:
    ctypes = item_content_types()
    excluded = {ctypes[cls] for cls in EXCLUDED_CAPTCHA_CLASSES if cls in ctypes}
    return [
        ctypes[cls]
        for cls in item_categories()[category]
        if cls in ctypes and ctypes[cls] not in excluded
    ]


def _covered_live_items(pks: list[int] | None = None):
    """Items with a real cover that are neither deleted nor merged away."""
    qs = Item.objects.filter(is_deleted=False, merged_to_item__isnull=True)
    if pks is not None:
        qs = qs.filter(pk__in=pks)
    return qs.exclude(cover="").exclude(cover=settings.DEFAULT_ITEM_COVER)


def build_pool(category: ItemCategory, popular: bool) -> list[int]:
    """Build and cache one candidate pk list for a category.

    ``popular`` selects the marks-based pool; otherwise every eligible item in
    the category is a candidate, which is the fallback for a thin catalog.
    """
    ctype_ids = _category_ctype_ids(category)
    if not ctype_ids:
        return []
    if popular:
        marked = (
            ShelfMember.objects.filter(q_item_in_category(category))
            # narrow to eligible classes *inside* the aggregate: ranking first
            # and filtering after lets excluded sub-items (seasons, episodes)
            # consume the whole limit, which would drop genuinely popular
            # items and push the draw onto obscure fallback ones
            .filter(item__polymorphic_ctype_id__in=ctype_ids)
            .values("item_id")
            .annotate(num=Count("item_id"))
            .filter(num__gte=SiteConfig.system.min_marks_for_captcha)
            .order_by("-num")[:CAPTCHA_POOL_LIMIT]
        )
        item_ids = [m["item_id"] for m in marked]
        if not item_ids:
            return []
        pks = list(
            _covered_live_items(item_ids)
            .filter(polymorphic_ctype_id__in=ctype_ids)
            .values_list("pk", flat=True)
        )
    else:
        pks = list(
            _covered_live_items()
            .filter(polymorphic_ctype_id__in=ctype_ids)
            .order_by("-id")[:CAPTCHA_POOL_LIMIT]
            .values_list("pk", flat=True)
        )
    return pks


def _pool(category: ItemCategory, popular: bool) -> list[int]:
    """Read a cached pool, building it inline when the cache is cold."""
    prefix = _POPULAR_CACHE_PREFIX if popular else _ALL_CACHE_PREFIX
    key = f"{prefix}_{category.value}"
    pks = cache.get(key)
    if pks is None:
        pks = build_pool(category, popular)
        cache.set(key, pks, timeout=CAPTCHA_POOL_TTL)
    return pks


def refresh_pools() -> dict[str, int]:
    """Rebuild every pool. Called by the daily job."""
    sizes = {}
    for category in eligible_categories():
        for popular in (True, False):
            prefix = _POPULAR_CACHE_PREFIX if popular else _ALL_CACHE_PREFIX
            key = f"{prefix}_{category.value}"
            pks = build_pool(category, popular)
            cache.set(key, pks, timeout=CAPTCHA_POOL_TTL)
            sizes[key] = len(pks)
    return sizes


def _draw_items(category: ItemCategory, count: int, nonce: str) -> list[Item]:
    """Draw `count` distinct live, covered items of a category.

    Samples the popular pool first and tops up from the all-items pool. Pool
    entries can go stale within the day they are cached, so the loaded objects
    are re-checked and any that no longer qualify are dropped; oversampling
    keeps that from costing another query.
    """
    popular = list(_pool(category, popular=True))
    random.shuffle(popular)
    known = set(popular)
    topup = [pk for pk in _pool(category, popular=False) if pk not in known]
    random.shuffle(topup)
    candidates = (popular + topup)[: count * CAPTCHA_DRAW_OVERSAMPLE]
    if not candidates:
        return []
    items = list(_covered_live_items(candidates))
    random.shuffle(items)
    chosen: list[Item] = []
    for item in items:
        if len(chosen) >= count:
            break
        # render eagerly: a tile that 404s later would leave the visitor an
        # unanswerable quiz and cost them a regeneration. The bytes are cached,
        # so the request that serves this tile does no extra work.
        if item.has_cover() and render_tile(item, nonce) is not None:
            chosen.append(item)
    return chosen


def render_tile(item: Item, nonce: str) -> bytes | None:
    """Render one cover to the uniform tile image, or None if it cannot be.

    Normalization is a security requirement, not cosmetics: cover paths are
    ``item/<category>/...`` and shapes differ by category (square album art,
    2:3 posters, tall book covers, landscape box art), so an un-normalized tile
    leaks the answer through its dimensions and mime type just as plainly as
    through its URL.

    The cover is stretched to fill the square, and the alternatives were both
    worse:

    - fitting and padding keeps the shape perfectly readable. Flat bars have a
      measurable bounding box, so a bot recovers the aspect ratio, and with it
      the category, without looking at the picture at all.
    - cropping to a square hides the shape too, but with no title beneath it a
      crop that takes the top off a book cover can leave the tile unanswerable.

    Stretching distorts a little and leaves no geometry to measure: every tile
    is the same full-bleed square with all of the cover still visible. A person
    reads a squashed poster as a poster; a bot has to classify the image.

    The transform is also jittered per challenge. A fixed transform would be
    reproducible: the anonymous catalog search hands out an item's cover URL
    alongside its category, so a crawler could run this exact pipeline over the
    catalog and byte-match each tile, recovering everything the tokens hide.
    Jitter does not stop perceptual hashing -- nothing here does -- but it does
    stop a plain checksum join, which is the cheap version of the attack.
    """
    key = f"{_TILE_CACHE_PREFIX}_{nonce}_{item.pk}"
    data = cache.get(key)
    if data is not None:
        return data or None
    try:
        with item.cover.open("rb") as f:
            raw = f.read()
        source = Image.open(io.BytesIO(raw))
        source.load()
        source = source.convert("RGB")
        # derive the jitter from the challenge so a reload of the same quiz is
        # stable (and cacheable) while a different quiz renders differently
        seed = hashlib.sha256(f"{nonce}:{item.pk}".encode()).digest()
        inset = seed[0] % (CAPTCHA_TILE_JITTER_PX + 1)
        quality = CAPTCHA_TILE_QUALITY_MIN + (
            seed[1] % (CAPTCHA_TILE_QUALITY_MAX - CAPTCHA_TILE_QUALITY_MIN + 1)
        )
        if inset and source.width > 2 * inset and source.height > 2 * inset:
            source = source.crop(
                (inset, inset, source.width - inset, source.height - inset)
            )
        canvas = source.resize(
            (CAPTCHA_TILE_PX, CAPTCHA_TILE_PX), Image.Resampling.LANCZOS
        )
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
    except Exception as e:
        # vector covers and unreadable files are simply not usable as tiles;
        # cache the miss briefly so a broken cover is not retried per request
        logger.debug(f"Captcha tile render failed for item {item.pk}: {e}")
        cache.set(key, b"", timeout=CAPTCHA_TILE_TTL)
        return None
    cache.set(key, data, timeout=CAPTCHA_TILE_TTL)
    return data


def _fail_open(reason: str) -> None:
    cache.set(_FAIL_OPEN_CACHE_KEY, True, timeout=CAPTCHA_FAIL_OPEN_TTL)
    if cache.add(_WARN_CACHE_KEY, True, timeout=CAPTCHA_FAIL_OPEN_TTL):
        logger.warning(f"Registration captcha disabled for now: {reason}")


def _build_challenge(started: int, regens: int) -> Challenge | None:
    """Draw two categories and their tiles, or None to fail open."""
    total = SiteConfig.system.registration_captcha_items
    if total < 2:
        # a single tile cannot be split across two rows; the settings validator
        # rejects this, so only a hand-edited config reaches here
        return None
    categories = eligible_categories()
    random.shuffle(categories)
    # minted up front so the eager tile render below warms the same cache key
    # the tile requests will use
    nonce = secrets.token_urlsafe(16)

    for i, first in enumerate(categories):
        for second in categories[i + 1 :]:
            # Draw as many as either row could possibly need, then pick from
            # the splits the draw can actually support. Drawing for one random
            # split instead would fail open on a pair that had enough items all
            # along: two categories with two items each can only serve a 2/2
            # split, so a 1/3 draw would spuriously give up.
            available = {c: _draw_items(c, total - 1, nonce) for c in (first, second)}
            feasible = [
                a
                for a in range(1, total)
                if a <= len(available[first]) and total - a <= len(available[second])
            ]
            if not feasible:
                continue
            a = random.choice(feasible)
            drawn = {
                first: available[first][:a],
                second: available[second][: total - a],
            }
            rows = [first.value, second.value]
            # shuffle before assigning tokens: drawing per category would
            # otherwise leave the tiles grouped by row in insertion order,
            # which hands the answer to anyone reading the page source
            pairs = [
                (item.pk, rows.index(c.value))
                for c, its in drawn.items()
                for item in its
            ]
            random.shuffle(pairs)
            tiles: dict[str, Tile] = {
                secrets.token_urlsafe(16): {"pk": pk, "row": row} for pk, row in pairs
            }
            return {
                "started": started,
                "nonce": nonce,
                "regens": regens,
                "rows": rows,
                "tiles": tiles,
                "msg": None,
            }
    return None


def get_challenge(request: HttpRequest) -> Challenge | None:
    data = request.session.get(SESSION_KEY)
    if isinstance(data, dict) and data.get("tiles"):
        return cast(Challenge, data)
    return None


def _store(request: HttpRequest, challenge: Challenge) -> None:
    request.session[SESSION_KEY] = challenge
    request.session.modified = True


def ensure_challenge(request: HttpRequest) -> Challenge | None:
    """Return the live challenge, creating one on first entry.

    A repeat GET must not re-roll: otherwise a caller can keep asking until it
    draws a pool it has memorized. Returns None when the catalog cannot supply
    a fair quiz, which means fail open.
    """
    existing = get_challenge(request)
    if existing:
        return existing
    if cache.get(_FAIL_OPEN_CACHE_KEY):
        return None
    challenge = _build_challenge(started=_now(), regens=0)
    if not challenge:
        _fail_open("not enough covered items in two categories")
        return None
    _store(request, challenge)
    return challenge


class Regenerated(TypedDict):
    """Outcome of a regeneration attempt.

    ``challenge`` is None when the catalog could not build a fresh quiz, which
    the caller must treat as fail-open rather than as "try again": leaving the
    spent challenge in the session would let its nonce be submitted against
    indefinitely without ever consuming a regeneration.
    """

    challenge: Challenge | None
    exhausted: bool


def regenerate(request: HttpRequest, msg: str | None = None) -> Regenerated:
    """Spend one regeneration and issue a fresh quiz.

    The time budget is deliberately carried over: it covers the whole step.
    """
    current = get_challenge(request)
    if not current:
        return {"challenge": ensure_challenge(request), "exhausted": False}
    if current["regens"] >= CAPTCHA_MAX_REGENERATIONS:
        return {"challenge": None, "exhausted": True}
    challenge = _build_challenge(
        started=current["started"], regens=current["regens"] + 1
    )
    if not challenge:
        # drop the spent challenge: a stale nonce left in the session is an
        # unlimited-guess oracle, since submissions against it never advance
        # the regeneration count
        request.session.pop(SESSION_KEY, None)
        request.session.modified = True
        _fail_open("not enough covered items in two categories")
        return {"challenge": None, "exhausted": False}
    challenge["msg"] = msg
    _store(request, challenge)
    return {"challenge": challenge, "exhausted": False}


def claim_nonce(nonce: str) -> bool:
    """Atomically consume a challenge nonce; False if already used.

    Session writes are last-write-wins, so comparing the nonce and then
    rotating it in a later write lets concurrent posts each pass the check and
    collapse into a single spent regeneration -- effectively unlimited parallel
    guesses. cache.add is atomic, which is the same guard login_proof uses for
    solution replay.
    """
    if not nonce:
        return False
    return cache.add(f"{_NONCE_CACHE_PREFIX}:{nonce}", True, timeout=CAPTCHA_TTL)


def regenerations_left(challenge: Challenge) -> int:
    return max(0, CAPTCHA_MAX_REGENERATIONS - challenge["regens"])


def seconds_left(challenge: Challenge) -> int:
    return max(0, challenge["started"] + CAPTCHA_TTL - _now())


def expired(challenge: Challenge) -> bool:
    return seconds_left(challenge) <= 0


def pop_message(request: HttpRequest) -> str | None:
    challenge = get_challenge(request)
    if not challenge:
        return None
    msg = challenge.get("msg")
    if msg:
        challenge["msg"] = None
        _store(request, challenge)
    return msg


def tile_item(request: HttpRequest, token: str) -> tuple[Item, str] | None:
    """Resolve a tile token against the *current, live* challenge only.

    Returns the item and the challenge nonce, which keys the rendered bytes.
    An expired challenge serves nothing: its state lingers in the session
    until some captcha-page request replaces it, and there is no reason to
    keep answering for it.
    """
    challenge = get_challenge(request)
    if not challenge or expired(challenge):
        return None
    tile = challenge["tiles"].get(token)
    if not tile:
        return None
    item = Item.objects.filter(pk=tile["pk"]).first()
    return (item, challenge["nonce"]) if item else None


def check_answer(challenge: Challenge, answer: Any) -> bool:
    """Every tile must be assigned, and to its own row."""
    if not isinstance(answer, dict):
        return False
    tiles = challenge["tiles"]
    if set(answer.keys()) != set(tiles.keys()):
        return False
    for token, row in answer.items():
        if not isinstance(row, int) or row != tiles[token]["row"]:
            return False
    return True


def clear(request: HttpRequest) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.pop(PASSED_KEY, None)
    request.session.pop(CELEBRATE_KEY, None)


def mark_passed(request: HttpRequest, celebrate: bool = False) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session[PASSED_KEY] = _now()
    if celebrate:
        request.session[CELEBRATE_KEY] = True


def pop_celebration(request: HttpRequest) -> bool:
    """Consume the one-shot 'you just solved it' marker.

    One-shot so a reload or a back-button visit does not replay the moment,
    and so a fail-open pass -- which the visitor never earned -- does not get
    congratulated.
    """
    if not request.session.pop(CELEBRATE_KEY, False):
        return False
    request.session.modified = True
    return True


def has_passed(request: HttpRequest) -> bool:
    """True while a solved pass is still fresh.

    The pass is stamped rather than a bare flag: the session cookie lives for
    months, and a solve parked for that long has nothing to do with the
    five-minute budget it was earned under. It still buys exactly one account
    either way, since auth_login consumes it.
    """
    at = request.session.get(PASSED_KEY)
    if at is True:
        # a pass stored by an older build, before this was stamped
        return True
    if not isinstance(at, int):
        return False
    return _now() - at <= CAPTCHA_PASS_TTL


# --- interaction telemetry -------------------------------------------------
#
# The trace is client-supplied and therefore forgeable: these checks only cost
# automation that never simulates a pointer at all. They are kept deliberately
# loose, because a false rejection costs a real person their signup, and every
# threshold is a module constant so an operator can relax them. Setting
# REQUIRE_HUMAN_TRAJECTORY to False skips the path checks entirely.

REQUIRE_HUMAN_TRAJECTORY = True
CAPTCHA_PAYLOAD_MAX_LENGTH = 16_384
CAPTCHA_MAX_TRACE_POINTS = 200
CAPTCHA_MIN_DRAG_POINTS = 3
CAPTCHA_MIN_DRAG_MS = 100.0
CAPTCHA_MIN_CLICK_MS = 80.0
# A brisk but genuine sort of four tiles can finish inside a second and a half,
# so this floor only rules out submissions with essentially no interaction.
CAPTCHA_MIN_TOTAL_MS = 700.0
# shape checks need enough of a path to mean anything; see _check_entry
CAPTCHA_SHAPE_MIN_POINTS = 5
CAPTCHA_SHAPE_MIN_SPAN_PX = 30.0
CAPTCHA_MIN_DEVIATION_PX = 0.5

TRACE_MODES = frozenset({"drag", "click"})


def _max_perpendicular_deviation(points: list[tuple[float, float, float]]) -> float:
    """Largest distance from any sample to the straight start-to-end line.

    Naive automation interpolates linearly between two corners, which puts
    every sample on that line. No real touch or trackpad drag does.
    """
    (x0, y0, _), (x1, y1, _) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    span = (dx * dx + dy * dy) ** 0.5
    if span == 0:
        # start and end coincide: any movement in between is deviation
        return max(((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 for x, y, _ in points)
    return max(abs(dx * (y0 - y) - dy * (x0 - x)) / span for x, y, _ in points)


def _parse_points(raw: Any) -> list[tuple[float, float, float]] | None:
    if not isinstance(raw, list) or len(raw) > CAPTCHA_MAX_TRACE_POINTS:
        return None
    points: list[tuple[float, float, float]] = []
    for p in raw:
        if not isinstance(p, list | tuple) or len(p) != 3:
            return None
        try:
            x, y, t = (float(v) for v in p)
        except TypeError, ValueError:
            return None
        points.append((x, y, t))
    return points


def _check_entry(entry: Any) -> tuple[bool, str, float]:
    """Validate one tile's telemetry. Returns (ok, reason, duration_ms)."""
    if not isinstance(entry, dict):
        return False, "malformed", 0.0
    if entry.get("mode") not in TRACE_MODES:
        return False, "bad_mode", 0.0
    mode = entry["mode"]
    points = _parse_points(entry.get("points", []))
    if points is None:
        return False, "malformed", 0.0

    times = [t for _, _, t in points]
    # equal stamps are legal: coalesced pointer events share a timestamp
    if any(b < a for a, b in pairwise(times)):
        return False, "time_travel", 0.0
    duration = (times[-1] - times[0]) if len(times) > 1 else 0.0
    if isinstance(entry.get("duration"), int | float):
        duration = max(duration, float(entry["duration"]))

    if mode == "click":
        if duration < CAPTCHA_MIN_CLICK_MS:
            return False, "dwell", duration
        return True, "", duration

    if not REQUIRE_HUMAN_TRAJECTORY:
        return True, "", duration
    if len(points) < CAPTCHA_MIN_DRAG_POINTS:
        return False, "too_few_samples", duration
    if duration < CAPTCHA_MIN_DRAG_MS:
        return False, "dwell", duration

    # Both shape checks below need enough samples to say anything. On a short
    # drag with three points, one intermediate sample sitting near the
    # start-to-end line is ordinary, and two intervals coming out equal is
    # ordinary too on a browser that coarsens timer resolution. Judging those
    # would reject people, so only apply them once the path is long enough to
    # carry a real signal.
    if len(points) >= CAPTCHA_SHAPE_MIN_POINTS:
        intervals = [b - a for a, b in pairwise(times)]
        if len(set(intervals)) <= 1:
            return False, "uniform_intervals", duration
        (x0, y0, _), (x1, y1, _) = points[0], points[-1]
        span = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if (
            span >= CAPTCHA_SHAPE_MIN_SPAN_PX
            and _max_perpendicular_deviation(points) <= CAPTCHA_MIN_DEVIATION_PX
        ):
            return False, "collinear", duration
    return True, "", duration


def check_trace(challenge: Challenge, trace: Any) -> tuple[bool, str]:
    """Validate the telemetry for every tile. Returns (ok, reason)."""
    if not isinstance(trace, dict):
        return False, "malformed"
    if set(trace.keys()) != set(challenge["tiles"].keys()):
        return False, "incomplete"
    durations = []
    for entry in trace.values():
        ok, reason, duration = _check_entry(entry)
        if not ok:
            return False, reason
        durations.append(duration)
    if not REQUIRE_HUMAN_TRAJECTORY:
        return True, ""
    if sum(durations) < CAPTCHA_MIN_TOTAL_MS:
        return False, "too_fast"
    # every tile taking the exact same float duration is a generator
    if len(durations) > 1 and len(set(durations)) == 1:
        return False, "duplicate_durations"
    return True, ""


# --- per-address failure cap ----------------------------------------------
#
# This, not the telemetry heuristics, is the lever that actually costs an
# attacker: the sorting answer is resolvable per tile by anyone willing to
# write a solver for this site, so the useful defence is bounding how many
# attempts one address gets. Mirrors mastodon.views.email.

CAPTCHA_MAX_FAILS = 10
CAPTCHA_FAIL_TTL = 3600
_FAIL_CACHE_PREFIX = "reg_captcha_fails"


def fails_exceeded(ip: str) -> bool:
    if not ip:
        return False
    return (cache.get(f"{_FAIL_CACHE_PREFIX}_{ip}") or 0) >= CAPTCHA_MAX_FAILS


def record_fail(ip: str) -> None:
    if not ip:
        return
    key = f"{_FAIL_CACHE_PREFIX}_{ip}"
    if cache.add(key, 1, timeout=CAPTCHA_FAIL_TTL):
        return
    try:
        cache.incr(key)
    except ValueError:
        # the entry expired between add and incr
        cache.set(key, 1, timeout=CAPTCHA_FAIL_TTL)


def verify_submission(
    challenge: Challenge, answer_raw: str, trace_raw: str
) -> tuple[bool, str, str]:
    """Check one submitted answer. Returns (ok, outcome, reason)."""
    if (
        len(answer_raw) > CAPTCHA_PAYLOAD_MAX_LENGTH
        or len(trace_raw) > CAPTCHA_PAYLOAD_MAX_LENGTH
    ):
        return False, "bad_trace", "oversized"
    try:
        answer = json.loads(answer_raw or "null")
        trace = json.loads(trace_raw or "null")
    except json.JSONDecodeError, UnicodeDecodeError:
        return False, "bad_trace", "malformed"
    if not check_answer(challenge, answer):
        return False, "wrong_answer", "wrong_answer"
    ok, reason = check_trace(challenge, trace)
    if not ok:
        return False, "bad_trace", reason
    return True, "passed", ""
