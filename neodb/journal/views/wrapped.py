import base64
import binascii
import datetime
import io
import json
import logging
import mimetypes
import re
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files import File
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.db import transaction
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from PIL import Image
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from catalog.common import SiteManager
from catalog.models import Item
from catalog.sites.fedi import FediverseInstance
from catalog.search.utils import enqueue_fetch, fetch_job, get_fetch_lock
from common.sentry import count as sentry_count
from common.sentry import record_activity
from common.utils import AuthedHttpRequest, clean_json, target_identity_required
from journal.jobs.wrapped import enqueue_deck_refresh, refresh_state
from journal.models.common import VisibilityType
from journal.models.wrapped import (
    ALL_HIDDEN,
    AUDIENCES,
    MADE_CARD_ITEMS_MAX,
    MADE_CARDS_MAX,
    WrappedDeck,
)
from journal.wrapped.aggregates import (
    aggregates_ready,
)
from journal.wrapped.base import (
    CATEGORIES,
    _member,
    item_ref,
)
from journal.wrapped.data import (
    live_year,
)
from journal.wrapped.record import (
    auto_update_open,
    can_make,
    deck_for,
    default_year,
    latest_year,
    made_data,
    make_state,
    make_window,
    owner_years,
    stored_payload,
)
from journal.wrapped.words import (
    WORDS_KINDS,
    words_menu,
)
from journal.wrapped.music import BY_ID as MUSIC, menu as music_menu, track
from journal.wrapped.text import card_text
from mastodon.models.bluesky import EmbedObj
from takahe.utils import Takahe
from users.models import APIdentity

logger = logging.getLogger(__name__)

CARD_ID = re.compile(r"^[a-z0-9-]{1,40}$")
IMAGES_MAX = 4
IMAGE_BYTES_MAX = 4 * 1024 * 1024
# a video of every card: about 0.3 MB a card, and a deck has at most about 60 cards
VIDEO_BYTES_MAX = 20 * 1024 * 1024


def _year(year: int | None) -> int:
    if not year or year < 1970 or year > latest_year():
        return default_year()
    return year


def _admin_any_time(user: Any, year: int) -> bool:
    """A superuser may make and update a deck outside the window, to test it on the live site."""
    return bool(getattr(user, "is_superuser", False)) and 1970 <= year <= latest_year()


def _may_make(user: Any, year: int) -> bool:
    return can_make(year) or _admin_any_time(user, year)


def _session_tz(request: AuthedHttpRequest) -> str:
    """The time zone the browser reported, when it is a real one; else empty (the site's own)."""
    tz = request.session.get("detected_tz")
    if isinstance(tz, str) and tz:
        try:
            ZoneInfo(tz)
            return tz
        except ZoneInfoNotFoundError, ValueError:
            pass
    return ""


def _with_titles(deck: WrappedDeck) -> list[int]:
    """The audiences whose stored cards have titles."""
    return [
        lv for lv in AUDIENCES if ((deck.data or {}).get(str(lv)) or {}).get("items")
    ]


def _og(owner: APIdentity, year: int, payload: dict[str, Any]) -> dict[str, str]:
    """What a link preview shows: from the cards of this page's audience only (a crawler is signed out, so it
    gets the public cards)."""
    site = settings.SITE_INFO["site_url"].rstrip("/")
    n = len(payload.get("items") or [])
    name = owner.display_name
    return {
        "title": _("%(name)s’s %(year)s, card by card") % {"name": name, "year": year},
        "description": ngettext(
            "%(name)s finished %(n)d title in %(year)d.",
            "%(name)s finished %(n)d titles in %(year)d.",
            n,
        )
        % {"name": name, "n": n, "year": year},
        "image": avatar
        if (avatar := owner.avatar).startswith("http")
        else site + avatar,
        "url": payload["page_url"],
    }


def _made_for(deck: WrappedDeck, level: int) -> list[dict[str, Any]]:
    """The made cards a visitor at this level can see; the others never leave the server."""
    return [m for m in deck.made if deck.may_show(level, f"made-{m.get('id')}", True)]


def _context(
    owner: APIdentity,
    deck: WrappedDeck,
    year: int,
    level: int,
    is_owner: bool,
    viewer: Any = None,
) -> dict[str, Any]:
    # a visitor gets only the made cards their audience may see; the others, and their texts, stay here
    made = deck.made if is_owner else _made_for(deck, level)
    # the cards show what the last update stored
    payload = stored_payload(
        deck, level, viewer=viewer, is_owner=is_owner, picker=is_owner
    )
    state = deck.state(level)
    # a "your words" card carries one text, and only an audience allowed to see that text gets it: the
    # stored texts are those of this level, and a visitor gets only those of the made cards they may see
    ids = {str(m.get("id")) for m in made}
    words = {k: v for k, v in payload["words"].items() if k in ids}
    if not is_owner:
        # likewise only the collections of the made cards this visitor may see
        refs = {
            str((m.get("source") or {}).get("uuid") or "")
            for m in made
            if (m.get("source") or {}).get("type") == "collection"
        }
        payload["collections"] = [
            c for c in payload["collections"] if c["uuid"] in refs
        ]
        state["made"] = [
            m
            for m in made
            if (m.get("source") or {}).get("type") != "words"
            or str(m.get("id")) in words
        ]
    payload["words"] = words
    payload["deck"] = state
    payload["level"] = level
    # a visitor sees the list the owner last saw for this audience; without one the browser applies the rules and overrides
    payload["shown"] = None if is_owner else deck.shown_for(level)
    payload["owner_view"] = is_owner
    payload["page_url"] = settings.SITE_INFO["site_url"].rstrip("/") + reverse(
        "journal:wrapped_page", args=[owner.handle, year]
    )
    # covers on another host are read for exports through this origin, by the file the stored link names
    payload["cover_url"] = reverse("journal:wrapped_cover", args=["x"])[:-1]
    payload["media_prefix"] = _media_prefix()
    # the viewer's own year page: "see the whole year" for the owner, "make your own" for a visitor only while
    # the cards of this year can still be made
    if is_owner or _may_make(viewer, year):
        payload["make_url"] = reverse("journal:wrapped", args=[year])
    # the cards are drawn in the browser, in the language of the page
    payload["lang"] = get_language() or "en"
    payload["text"] = card_text()
    # every viewer gets the owner's choice of music, and hears it only after turning the sound on
    payload["music"] = track(deck.music)
    if is_owner:
        payload["urls"] = {
            "save": reverse("journal:wrapped_save", args=[year]),
            "share": reverse("journal:wrapped_share", args=[year]),
            "lookup": reverse("journal:wrapped_lookup"),
            "words": reverse("journal:wrapped_words", args=[year]),
            "refresh": reverse("journal:wrapped_refresh", args=[year]),
            "status": reverse("journal:wrapped_status", args=[year]),
            "preferences": reverse("users:preferences"),
        }
        # a queued or running update: the page waits for it and reloads with the new data; a job that ends
        # with this time unchanged did not store anything
        updating = refresh_state(deck.pk) == "updating"
        if not updating:
            # a job may have stored its data after this page read the deck, and cleared its marker before the
            # state was read: the page waits then too, and reloads at once
            updating = (
                WrappedDeck.objects.filter(pk=deck.pk)
                .values_list("refreshed_time", flat=True)
                .first()
                != deck.refreshed_time
            )
        payload["updating"] = updating
        payload["update_time"] = (
            deck.refreshed_time.isoformat() if deck.refreshed_time else None
        )
        payload["music_menu"] = music_menu()
        payload["years"] = owner_years(owner)
        payload["update_open"] = _may_make(viewer, year)
        payload["made_max"] = MADE_CARDS_MAX
        levels_with_titles = _with_titles(deck)
        if not payload["items"]:
            # an audience with no titles: the empty page offers the audiences that have some
            payload["levels_with_titles"] = levels_with_titles
        # whether people who are not signed in can open the page at all (the QR code tab warns when not)
        payload["anon_ok"] = bool(owner.anonymous_viewable)
        # what each audience sees, for the Share dialog: no count for an audience the owner has not opened
        payload["audiences"] = [
            {
                "level": lv,
                "off": lv not in levels_with_titles or deck.is_off(lv),
                "shown": len(sh) if (sh := deck.shown_for(lv)) is not None else None,
            }
            for lv in AUDIENCES
        ]
    ctx = {"identity": owner, "year": year, "payload": payload, "is_owner": is_owner}
    if is_owner:
        # the Card data part of the Edit dialog: when the data was stored, and whether it still updates
        ctx["update"] = {
            "open": can_make(year),
            "admin": not can_make(year) and _admin_any_time(viewer, year),
            "time": deck.refreshed_time,
            "auto": auto_update_open(year),
            "last": make_window(year)[1] - datetime.timedelta(days=1),
        }
    return ctx


def _deck_path(owner: APIdentity, year: int) -> str:
    return reverse("journal:wrapped_page", args=[owner.handle, year])


@login_required
@require_GET
def wrapped_year(request: AuthedHttpRequest, year: int | None = None):
    """The owner's year page: the whole year, read now, with every title (it is the owner's alone), and the
    step to make the cards or the link to them. The cards themselves are on the deck page."""
    y = _year(year)
    if year != y:
        return HttpResponseRedirect(reverse("journal:wrapped", args=[y]))
    owner = request.user.identity
    deck = deck_for(owner, y)
    making = ""
    if deck is not None and deck.refreshed_time is None:
        # not made yet: its job is queued or running, or it failed and the step is offered again
        making = refresh_state(deck.pk)
        deck = None
    # the deck's time zone once it has one, so the year page and the cards cut the year alike
    tz = WrappedDeck(
        owner=owner,
        year=y,
        timezone=(deck.timezone if deck else "") or _session_tz(request),
    ).tzinfo
    payload = live_year(owner, y, 2, tz)
    start, end = make_window(y)
    payload.update(
        {
            "year": y,
            "member": _member(owner),
            "history": None,
            "friends": [],
            "level": 2,
            "owner_view": True,
            "deck": {"order": [], "hidden": {}, "made": []},
            "shown": None,
            "words": {},
            "lang": get_language() or "en",
            "text": card_text(),
            "make_url": reverse("journal:wrapped", args=[y]),
            "years": owner_years(owner),
            "year_page": True,
            "deck_made": deck is not None,
            "making": making == "updating",
        }
    )
    if making == "updating":
        payload["urls"] = {"status": reverse("journal:wrapped_status", args=[y])}
        payload["deck_url"] = _deck_path(owner, y)
    ctx = {
        "identity": owner,
        "year": y,
        "payload": payload,
        "is_owner": True,
        "year_page": {
            "deck_url": _deck_path(owner, y) if deck else None,
            "making": making == "updating",
            "failed": making == "failed",
            "state": make_state(y),
            "admin": not can_make(y) and _admin_any_time(request.user, y),
            "auto": auto_update_open(y),
            "start": start,
            "last": end - datetime.timedelta(days=1),
        },
    }
    resp = render(request, "wrapped_year.html", ctx)
    # no_store: the browser keeps no copy to show again after sign-out
    patch_cache_control(resp, private=True, no_store=True)
    return resp


@login_required
@require_POST
def wrapped_generate(request: AuthedHttpRequest, year: int):
    """Make the deck of a year, once, from 1 December to the end of January after it: a job stores the card
    data of each audience, which the cards then show until the next update, and the year page waits for it."""
    owner = request.user.identity
    if not _may_make(request.user, year):
        return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))
    deck = deck_for(owner, year, create=True)
    assert deck is not None
    with transaction.atomic():
        deck = WrappedDeck.objects.select_for_update().get(pk=deck.pk)
        if not deck.timezone and (tz := _session_tz(request)):
            deck.timezone = tz
            deck.save(update_fields=["timezone", "edited_time"])
    # queued once the time zone is committed, as the job reads it; a second submit finds the job queued
    if deck.refreshed_time is None:
        if enqueue_deck_refresh(deck, make=True):
            record_activity("annual", "web")
        return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))
    return HttpResponseRedirect(_deck_path(owner, year))


@login_required
@require_POST
def wrapped_delete(request: AuthedHttpRequest, year: int):
    """Delete the owner's deck of a year with all their choices for it; posts made from it stay. While decks
    can be made (to the end of January) the owner can make a new one; after that the year has none."""
    WrappedDeck.objects.filter(owner=request.user.identity, year=year).delete()
    return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))


def _made_deck(owner: APIdentity, year: int, lock: bool = False) -> WrappedDeck:
    """The owner's deck of the year once it is made, without its stored data (which these views do not
    read); a 404 before that."""
    qs = WrappedDeck.objects.defer("data")
    if lock:
        qs = qs.select_for_update()
    deck = qs.filter(owner=owner, year=year, refreshed_time__isnull=False).first()
    if deck is None:
        raise Http404(_("This summary is not made yet."))
    return deck


@login_required
@require_POST
def wrapped_refresh(request: AuthedHttpRequest, year: int):
    """Queue a job that stores the card data again, while updates are open (to the end of January); at most
    once a minute, as each update reads the whole year three times. The page asks ``wrapped_status`` when it
    is done."""
    deck = _made_deck(request.user.identity, year)
    if not _may_make(request.user, year):
        return JsonResponse({"ok": False, "status": "closed"}, status=400)
    if refresh_state(deck.pk) == "updating":
        return JsonResponse({"ok": True, "status": "queued"})
    if not cache.add(f"wrapped:refresh:{deck.pk}", 1, 60):
        return JsonResponse({"ok": False, "status": "busy"}, status=429)
    if enqueue_deck_refresh(deck):
        record_activity("annual", "web")
    return JsonResponse({"ok": True, "status": "queued"})


@login_required
@require_GET
def wrapped_status(request: AuthedHttpRequest, year: int):
    """Whether the owner's deck of a year is made, and whether a job that stores its card data is queued or
    running or has just failed: the year page and the Edit sheet wait on this."""
    deck = (
        WrappedDeck.objects.defer("data")
        .filter(owner=request.user.identity, year=year)
        .first()
    )
    state = refresh_state(deck.pk) if deck else ""
    if deck and state != "updating":
        # a job may have stored its time and cleared its marker between the two reads; it stores before it
        # clears, so a second read has the time
        deck = WrappedDeck.objects.defer("data").filter(pk=deck.pk).first()
    resp = JsonResponse(
        {
            "made": bool(deck and deck.refreshed_time),
            "updating": state == "updating",
            "failed": state == "failed",
            "time": deck.refreshed_time.isoformat()
            if deck and deck.refreshed_time
            else None,
        }
    )
    patch_cache_control(resp, private=True, no_store=True)
    return resp


def _owner_level(request: AuthedHttpRequest, deck: WrappedDeck) -> int:
    """The audience the owner views and edits: the one asked for, else the widest one that is on and has
    titles, so the owner never opens on an empty preview while their titles sit in a narrower one."""
    try:
        level = int(request.GET.get("as", ""))
    except ValueError:
        level = -1
    if level in AUDIENCES:
        return level
    on = [lv for lv in AUDIENCES if not deck.is_off(lv)]
    with_titles = _with_titles(deck)
    return next((lv for lv in on if lv in with_titles), on[0] if on else 2)


@require_GET
@target_identity_required
def wrapped_page(request: AuthedHttpRequest, user_name: str, year: int):
    """The deck: one link for everybody. A visitor sees the cards of their audience; the owner sees one
    audience at a time (``?as=N``) with the tools to edit and share them."""
    target = request.target_identity
    deck = deck_for(target, year)
    if deck is not None and deck.refreshed_time is None:
        deck = None
    if request.user.is_authenticated and target.user == request.user:
        if deck is None:
            return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))
        if not deck.timezone and (tz := _session_tz(request)):
            deck.timezone = tz
            deck.save(update_fields=["timezone", "edited_time"])
        # the deck was stored while another process was still computing the instance totals: a job fills the
        # gap, and the page reloads when it is done
        if (
            _may_make(request.user, year)
            and any(p.get("warming") for p in (deck.data or {}).values())
            and aggregates_ready(year)
        ):
            enqueue_deck_refresh(deck)
        level = _owner_level(request, deck)
        ctx = _context(target, deck, year, level, True, request.user)
    else:
        viewer = request.identity
        # a follower sees the followers' cards, or everyone's when the member shows followers none; anybody else sees everyone's
        levels = [1, 0] if viewer and viewer.is_following(target) else [0]
        level = next((lv for lv in levels if deck and not deck.is_off(lv)), None)
        if deck is None or level is None:
            raise Http404(_("This summary is not shared."))
        ctx = _context(target, deck, year, level, False, request.user)
        ctx["og"] = _og(target, year, ctx["payload"])
    resp = render(request, "wrapped.html", ctx)
    # one link serves different cards to different viewers, so no shared cache may keep it, and the
    # browser keeps no copy to show again after the viewer loses access or signs out
    patch_cache_control(resp, private=True, no_store=True)
    return resp


def _clean_str(v: Any, size: int) -> str:
    return str(v or "")[:size]


def _clean_url(v: Any) -> str:
    u = str(v or "")[:500]
    # a path on this site, or an http(s) URL; "//host" and "/\host" name another host, as browsers read them
    if re.match(r"^/(?![/\\])", u) or re.match(r"^https?://", u):
        return u
    return ""


def _clean_cover(v: Any) -> str:
    """A cover from this site or its media storage, as the catalog gives them; else empty. A made card is
    shown to visitors, and a cover from anywhere else would have their browsers fetch it from that host."""
    u = _clean_url(v)
    own = [settings.SITE_INFO["site_url"].rstrip("/") + "/"]
    if re.match(r"^https?://", settings.MEDIA_URL):
        own.append(settings.MEDIA_URL)
    return u if u.startswith("/") or any(u.startswith(o) for o in own) else ""


def _clean_made(made: Any) -> list[dict[str, Any]]:
    out = []
    for m in made if isinstance(made, list) else []:
        if not isinstance(m, dict) or len(out) >= MADE_CARDS_MAX:
            continue
        src = m.get("source") if isinstance(m.get("source"), dict) else {}
        kind = src.get("type")
        if kind == "auto":
            cat = src.get("cat")
            source: dict[str, Any] = {
                "type": "auto",
                "cat": cat if cat in CATEGORIES else "all",
            }
        elif kind == "collection":
            if not src.get("uuid"):
                continue
            source = {"type": "collection", "uuid": _clean_str(src.get("uuid"), 40)}
        elif kind == "words":
            # one text of the member's on one title: a reference, so the card follows edits of the text
            if src.get("kind") not in WORDS_KINDS or not src.get("ref"):
                continue
            source = {
                "type": "words",
                "kind": src["kind"],
                "ref": _clean_str(src.get("ref"), 40),
            }
        elif kind == "hand":
            items = []
            for it in src.get("items") if isinstance(src.get("items"), list) else []:
                if (
                    isinstance(it, dict)
                    and len(items) < MADE_CARD_ITEMS_MAX
                    and _clean_url(it.get("url"))
                ):
                    items.append(
                        {
                            "url": _clean_url(it.get("url")),
                            "title": _clean_str(it.get("title"), 300),
                            "cover": _clean_cover(it.get("cover")),
                            "category": it.get("category")
                            if it.get("category") in CATEGORIES
                            else "book",
                        }
                    )
            source = {"type": "hand", "items": items}
        else:
            continue
        try:
            n = max(3, min(MADE_CARD_ITEMS_MAX, int(m.get("n") or 5)))
            mid = int(m.get("id") or 0)
        # json.loads reads Infinity as a float, and int() of it raises OverflowError
        except TypeError, ValueError, OverflowError:
            continue
        out.append(
            {
                "id": mid,
                "title": _clean_str(m.get("title"), 120),
                "caption": _clean_str(m.get("caption"), 80),
                "n": n,
                "source": source,
            }
        )
    return out


@login_required
@require_POST
def wrapped_save(request: AuthedHttpRequest, year: int):
    """Save the owner's choices for one audience (hidden and shown cards) and for all of them (order, made cards)."""
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("invalid json")
    if not isinstance(data, dict):
        return HttpResponseBadRequest("invalid json")
    try:
        aud = int(data.get("aud", -1))
    except TypeError, ValueError, OverflowError:
        aud = -1
    if aud not in AUDIENCES:
        return HttpResponseBadRequest("invalid audience")
    if isinstance(data.get("made"), list) and len(data["made"]) > MADE_CARDS_MAX:
        return HttpResponseBadRequest("too many cards")
    with transaction.atomic():
        # the audiences share the JSON columns, so two saves must not read and write them at the same time
        deck = _made_deck(request.user.identity, year, lock=True)
        error = _save_choices(deck, aud, data)
    return error or JsonResponse({"ok": True})


def _save_choices(
    deck: WrappedDeck, aud: int, data: dict[str, Any]
) -> HttpResponseBadRequest | None:
    """Apply one save to the locked deck; an invalid value saves nothing and returns the error."""
    fields = []
    if isinstance(data.get("order"), list):
        deck.order = [
            c for c in data["order"] if isinstance(c, str) and CARD_ID.match(c)
        ][:100]
        fields.append("order")
    if isinstance(data.get("hidden"), dict):
        hidden = dict(deck.hidden or {})
        hidden[str(aud)] = {
            str(k): bool(v)
            for k, v in list(data["hidden"].items())[:100]
            if k == ALL_HIDDEN or CARD_ID.match(str(k))
        }
        deck.hidden = hidden
        fields.append("hidden")
    if "made" in data:
        # the made cards are the only free text a save stores: NUL and lone surrogates would fail the jsonb write
        made = clean_json(_clean_made(data["made"]))
        if made != deck.made:
            deck.made = made
            fields.append("made")
            # a new or changed made card stores what it shows now, as the deck's other data does
            stored = dict(deck.data or {})
            for key, p in stored.items():
                stored[key] = {
                    **p,
                    "made_data": made_data(deck.owner, made, int(key), deck.tzinfo),
                }
            deck.data = stored
            fields.append("data")
    if "music" in data:
        music = str(data["music"] or "")
        if music and music not in MUSIC:
            return HttpResponseBadRequest("invalid music")
        deck.music = music
        fields.append("music")
    if isinstance(data.get("shown"), list):
        sent = [c for c in data["shown"] if isinstance(c, str) and CARD_ID.match(c)]
        saved = deck.shown_for(aud)
        if data.get("partial"):
            # the page lacked some cards (a job's result was not cached yet): the saved cards it does not
            # name stay, unless the owner has now hidden them (the overrides above are already applied);
            # with no saved list yet, none is made from an incomplete one
            if saved is None:
                sent = None
            else:
                sent += [
                    c
                    for c in saved
                    if c not in sent and deck.card_hidden(aud, c) is not True
                ]
        if sent is not None:
            shown = dict(deck.shown or {})
            shown[str(aud)] = sent[:100]
            deck.shown = shown
            fields.append("shown")
    if fields:
        deck.save(update_fields=fields + ["edited_time"])
    return None


def _png_ok(raw: bytes) -> bool:
    """A PNG that decodes and is not too large. Each image is decoded here, before any attachment row
    exists, so a broken one never fails part way through the upload and leaves the others behind."""
    if not raw.startswith(b"\x89PNG") or len(raw) > IMAGE_BYTES_MAX:
        return False
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
    except Exception:
        return False
    return True


@login_required
@require_POST
def wrapped_share(request: AuthedHttpRequest, year: int):
    """Post up to four cards as images, or a video of all of them, to the timeline with the member's note;
    with "crosspost" on, also to the member's linked Mastodon and Bluesky accounts, as a normal post does."""
    _made_deck(request.user.identity, year)
    comment = request.POST.get("comment", "")[:1000]
    crosspost = request.POST.get("crosspost") == "1"
    try:
        visibility = VisibilityType(int(request.POST.get("visibility", 0)))
        aud = int(request.POST.get("aud", 2))
    except ValueError:
        return HttpResponseBadRequest("invalid visibility")
    # the cards carry counts from their audience's level, so a post never reaches a wider audience than that
    if aud not in AUDIENCES or int(visibility) < aud:
        return HttpResponseBadRequest("invalid visibility")
    images: list[bytes] = []
    video = poster = None
    if request.POST.get("kind") == "video":
        video, poster_file = request.FILES.get("video"), request.FILES.get("poster")
        if not video or not video.size or video.size > VIDEO_BYTES_MAX:
            return HttpResponseBadRequest("invalid video")
        head = video.read(12)
        video.seek(0)
        if head[4:8] != b"ftyp":
            return HttpResponseBadRequest("invalid video")
        poster = poster_file.read(IMAGE_BYTES_MAX + 1) if poster_file else b""
        if not _png_ok(poster):
            return HttpResponseBadRequest("invalid image")
    else:
        for s in request.POST.getlist("img")[:IMAGES_MAX]:
            try:
                raw = base64.b64decode(s.split(",", 1)[-1], validate=True)
            except binascii.Error, ValueError:
                return HttpResponseBadRequest("invalid image")
            if not _png_ok(raw):
                return HttpResponseBadRequest("invalid image")
            images.append(raw)
        if not images:
            return HttpResponseBadRequest("no image")
    user = request.user
    identity = user.identity
    alt = _("{year} annual summary card").format(year=year)
    if video:
        # the video is already H.264 from the browser: stored as it is, with the intro card as its still
        media = [
            Takahe.upload_attachment(
                identity.pk,
                f"{year}-wrapped.mp4",
                File(video),
                "video/mp4",
                alt,
                poster=poster,
            )
        ]
    else:
        media = [
            Takahe.upload_image(
                identity.pk, f"{year}-{k + 1}.png", img, "image/png", alt
            )
            for k, img in enumerate(images)
        ]
    post = Takahe.post(
        identity.pk,
        comment,
        Takahe.visibility_n2t(visibility, user.preference.post_public_mode),
        attachments=media,
    )
    # a non-public post cannot be boosted, so it is always posted as a copy
    classic_crosspost = (
        user.preference.mastodon_repost_mode == 1 or visibility != VisibilityType.Public
    )
    # a video goes to the other sites as its still (the intro card); the note carries the page link
    cross_images = images or ([poster] if poster else [])
    if crosspost and classic_crosspost and user.mastodon:
        attrs = {"platform": "mastodon", "mode": "wrapped"}
        try:
            user.mastodon.post(
                comment,
                visibility,
                attachments=[
                    (f"{year}-{k + 1}.png", img, "image/png")
                    for k, img in enumerate(cross_images)
                ],
            )
            sentry_count("crosspost.success", attributes=attrs)
        except Exception as e:
            logger.warning(f"wrapped post to {user.mastodon} failed: {e}")
            sentry_count("crosspost.failure", attributes=attrs)
    elif crosspost and post and user.mastodon:
        assert post.url is not None
        user.mastodon.boost_later(post.url)
    if crosspost and visibility == VisibilityType.Public and user.bluesky:
        o = EmbedObj("🧩", "", user.absolute_url)
        txt = comment.rstrip() + "\n\n##obj##"
        attrs = {"platform": "bluesky", "mode": "wrapped"}
        try:
            user.bluesky.post(txt, obj=o, images=cross_images)
            sentry_count("crosspost.success", attributes=attrs)
        except Exception as e:
            logger.warning(f"wrapped post to {user.bluesky} failed: {e}")
            sentry_count("crosspost.failure", attributes=attrs)
    return JsonResponse({"ok": True, "url": post.url if post else None})


@login_required
@require_http_methods(["GET", "POST"])
def wrapped_lookup(request: AuthedHttpRequest):
    """Resolve a pasted link for Make a card: found, fetching, unsupported, missing or busy.

    Answering never contacts another site: a link is matched only against known site patterns and this
    instance's items, and a GET only reports what is known. A POST may start a fetch, which runs as a
    queued job under the usual fetch limits; the job also detects sites a pattern cannot, and polls follow
    its state, so a slow fetch keeps reporting "fetching" until it ends.
    """
    url = (request.POST if request.method == "POST" else request.GET).get("url", "")
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:  # an unmatched bracket in the host, for one
        return JsonResponse({"status": "invalid"})
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in url:
        return JsonResponse({"status": "invalid"})
    if FediverseInstance.is_local_item_url(url):
        item = Item.get_by_url(urlparse(url).path, resolve_merge=True)
        if not item or item.is_deleted:
            return JsonResponse({"status": "missing"})
        return JsonResponse({"status": "found", "item": item_ref(item)})
    site = SiteManager.get_site_by_url(
        url, detect_redirection=False, detect_fallback=False
    )
    item = site.get_item() if site else None
    if item and not item.is_deleted:
        return JsonResponse({"status": "found", "item": item_ref(item)})
    job = fetch_job(url)
    state = job.get_status() if job else None
    if state in ("queued", "started", "deferred", "scheduled"):
        return JsonResponse({"status": "fetching"})
    # a site was found but could not be read just now (a timeout, say): a POST may try again
    failed = False
    if state == "finished" and job:
        # the job returns the item's path, or "-" when no site can read the link or the fetch failed; the
        # job's meta tells the two apart, also for a site the worker found only through a redirect
        result = job.result if isinstance(job.result, str) else None
        if result and result != "-":
            item = Item.get_by_url(result, resolve_merge=True)
            if item and not item.is_deleted:
                return JsonResponse({"status": "found", "item": item_ref(item)})
            return JsonResponse({"status": "missing"})
        if not site and not (job.meta or {}).get("fetch_failed"):
            return JsonResponse({"status": "unsupported"})
        failed = True
    elif state == "failed":
        # the job itself failed (it timed out or the worker stopped), which says nothing about the site
        failed = True
    if request.method != "POST":
        return JsonResponse({"status": "error" if failed else "unknown"})
    if get_fetch_lock(request.user, url):
        # the fetch refuses unsafe links (private or loopback hosts), which no site can read
        queued = enqueue_fetch(url, False, request.user)
        return JsonResponse({"status": "fetching" if queued else "unsupported"})
    return JsonResponse({"status": "busy"})


@login_required
@require_GET
def wrapped_words(request: AuthedHttpRequest, year: int):
    """The texts the owner wrote in the deck year, by title, for the picker of the "your words" card."""
    owner = request.user.identity
    y = _year(year)
    deck = deck_for(owner, y)
    tz = deck.tzinfo if deck else ZoneInfo(settings.TIME_ZONE)
    return JsonResponse({"works": words_menu(owner, y, tz)})


@require_GET
def wrapped_avatar(request: HttpRequest, identity_id: int):
    """A local member's profile picture from this site's origin, for the corner of every card.

    It is served here for the same reason as the covers (see wrapped_cover). A member with no picture of
    their own gets a 404, and the cards then show the site icon.
    """
    identity = APIdentity.objects.filter(
        pk=identity_id, local=True, deleted__isnull=True
    ).first()
    ti = identity.takahe_identity if identity else None
    if not ti or not ti.icon:
        raise Http404()
    try:
        f = ti.icon.open("rb")
    except Exception:
        raise Http404()
    resp = FileResponse(f)
    # the link carries a version, so a new picture gets a new link
    resp["Cache-Control"] = "public, max-age=604800"
    return resp


def _media_prefix() -> str:
    """The absolute start of a media file's link: what a stored cover link has before the file's name."""
    u = default_storage.url("x").split("?")[0]
    if not u.startswith(("http://", "https://")):
        u = settings.SITE_INFO["site_url"].rstrip("/") + u
    return u[:-1]


# an item cover as catalog/models/utils.py:item_cover_path names it
COVER_FILE = re.compile(
    r"^item/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.(?:jpe?g|png|webp|gif)$", re.I
)


@require_GET
def wrapped_cover(request: HttpRequest, name: str):
    """A cover file served from this site's origin, so a card drawn into a canvas can be exported.

    On an instance whose media lives on another host, a cross-origin image would taint the canvas and block
    the downloads. The file is the one the stored card names, not the item's current cover, so an export
    matches the stored cards after the item changes or is merged.
    """
    if ".." in name or not COVER_FILE.match(name):
        raise Http404()
    try:
        if not default_storage.exists(name):
            raise Http404()
        f = default_storage.open(name, "rb")
    except Http404:
        raise
    except Exception:
        raise Http404()
    resp = FileResponse(f, content_type=mimetypes.guess_type(name)[0])
    resp["Cache-Control"] = "public, max-age=604800"
    return resp
