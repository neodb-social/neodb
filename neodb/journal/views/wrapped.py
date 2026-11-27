import base64
import binascii
import datetime
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
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
from django.core.files import File
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from catalog.common import SiteManager
from catalog.models import Item
from catalog.sites.fedi import FediverseInstance
from catalog.search.utils import enqueue_fetch, get_fetch_lock
from common.sentry import count as sentry_count
from common.utils import AuthedHttpRequest, target_identity_required
from journal.models.common import VisibilityType
from journal.models.wrapped import (
    ALL_HIDDEN,
    AUDIENCES,
    MADE_CARD_ITEMS_MAX,
    MADE_CARDS_MAX,
    WrappedDeck,
)
from journal.wrapped import CATEGORIES, build_payload, deck_for, friends_allowed
from journal.wrapped_music import BY_ID as MUSIC, menu as music_menu, track
from journal.wrapped_text import card_text
from mastodon.models.bluesky import EmbedObj
from takahe.utils import Takahe
from users.models import APIdentity

logger = logging.getLogger(__name__)

CARD_ID = re.compile(r"^[a-z0-9-]{1,40}$")
IMAGES_MAX = 4
IMAGE_BYTES_MAX = 4 * 1024 * 1024
# a video of every card: about 0.3 MB a card, and a deck has at most about 50 cards
VIDEO_BYTES_MAX = 16 * 1024 * 1024


def _year(year: int | None) -> int:
    this = datetime.date.today().year
    if not year or year < 2000 or year > this:
        return this
    return year


def _made_for(deck: WrappedDeck, level: int) -> list[dict[str, Any]]:
    """The made cards a visitor at this level can see; the others never leave the server."""
    shown = deck.shown_for(level)
    if shown is not None:
        return [m for m in deck.made if f"made-{m.get('id')}" in shown]
    return [
        m
        for m in deck.made
        if deck.card_hidden(level, f"made-{m.get('id')}") is not True
    ]


def _context(
    owner: APIdentity, deck: WrappedDeck, year: int, level: int, is_owner: bool
) -> dict[str, Any]:
    payload = build_payload(
        owner,
        year,
        level,
        deck.tzinfo,
        include_friends=friends_allowed(deck, level, is_owner),
    )
    state = deck.state(level)
    if not is_owner:
        state["made"] = _made_for(deck, level)
    payload["deck"] = state
    payload["level"] = level
    # a visitor sees the list the owner last saw for this audience; without one the browser applies the rules and overrides
    payload["shown"] = None if is_owner else deck.shown_for(level)
    payload["owner_view"] = is_owner
    payload["page_url"] = settings.SITE_INFO["site_url"].rstrip("/") + reverse(
        "journal:wrapped_page", args=[owner.handle, year]
    )
    payload["cover_url"] = reverse("journal:wrapped_cover", args=["UUID"])
    # the owner's own page for this year: the year switch for the owner, "make your own" for a visitor
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
        }
        payload["music_menu"] = music_menu()
        # what each audience sees, for the Share dialog: no count for an audience the owner has not opened
        payload["audiences"] = [
            {
                "level": lv,
                "off": deck.is_off(lv),
                "shown": len(sh) if (sh := deck.shown_for(lv)) is not None else None,
            }
            for lv in AUDIENCES
        ]
    return {"identity": owner, "year": year, "payload": payload, "is_owner": is_owner}


@login_required
@require_GET
def wrapped_deck(request: AuthedHttpRequest, year: int | None = None):
    y = _year(year)
    if year != y:
        return HttpResponseRedirect(reverse("journal:wrapped", args=[y]))
    owner = request.user.identity
    deck = deck_for(owner, y, create=True)
    assert deck is not None
    if not deck.timezone:
        tz = request.session.get("detected_tz")
        if isinstance(tz, str) and tz:
            try:
                ZoneInfo(tz)
                deck.timezone = tz
                deck.save(update_fields=["timezone", "edited_time"])
            except ZoneInfoNotFoundError, ValueError:
                pass
    # the audience the owner views and edits; by default the widest one that sees something
    try:
        level = int(request.GET.get("as", ""))
    except ValueError:
        level = -1
    if level not in AUDIENCES:
        level = next((lv for lv in AUDIENCES if not deck.is_off(lv)), 2)
    resp = render(request, "wrapped.html", _context(owner, deck, y, level, True))
    patch_cache_control(resp, private=True)
    return resp


@require_GET
@target_identity_required
def wrapped_page(request: AuthedHttpRequest, user_name: str, year: int):
    target = request.target_identity
    if request.user.is_authenticated and target.user == request.user:
        return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))
    deck = deck_for(target, year)
    viewer = request.identity
    # a follower sees the followers' cards, or everyone's when the member shows followers none; anybody else sees everyone's
    levels = [1, 0] if viewer and viewer.is_following(target) else [0]
    level = next((lv for lv in levels if deck and not deck.is_off(lv)), None)
    if deck is None or level is None:
        raise Http404(_("This summary is not shared."))
    resp = render(request, "wrapped.html", _context(target, deck, year, level, False))
    # one link serves different cards to different viewers, so no shared cache may keep it
    patch_cache_control(resp, private=True)
    return resp


def _clean_str(v: Any, size: int) -> str:
    return str(v or "")[:size]


def _clean_url(v: Any) -> str:
    u = str(v or "")[:500]
    if u.startswith("/") or re.match(r"^https?://", u):
        return u
    return ""


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
            source = {"type": "collection", "uuid": _clean_str(src.get("uuid"), 40)}
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
                            "cover": _clean_url(it.get("cover")),
                            "category": it.get("category")
                            if it.get("category") in CATEGORIES
                            else "book",
                            "note": _clean_str(it.get("note"), 300) or None,
                        }
                    )
            source = {"type": "hand", "items": items}
        else:
            continue
        try:
            n = max(3, min(MADE_CARD_ITEMS_MAX, int(m.get("n") or 5)))
            mid = int(m.get("id") or 0)
        except TypeError, ValueError:
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
    except TypeError, ValueError:
        aud = -1
    if aud not in AUDIENCES:
        return HttpResponseBadRequest("invalid audience")
    deck = deck_for(request.user.identity, _year(year), create=True)
    assert deck is not None
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
        deck.made = _clean_made(data["made"])
        fields.append("made")
    if "music" in data:
        music = str(data["music"] or "")
        if music and music not in MUSIC:
            return HttpResponseBadRequest("invalid music")
        deck.music = music
        fields.append("music")
    if isinstance(data.get("shown"), list):
        shown = dict(deck.shown or {})
        shown[str(aud)] = [
            c for c in data["shown"] if isinstance(c, str) and CARD_ID.match(c)
        ][:100]
        deck.shown = shown
        fields.append("shown")
    if fields:
        deck.save(update_fields=fields + ["edited_time"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def wrapped_share(request: AuthedHttpRequest, year: int):
    """Post up to four cards as images, or a video of all of them, to the timeline with the member's note."""
    comment = request.POST.get("comment", "")[:1000]
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
        if not poster.startswith(b"\x89PNG") or len(poster) > IMAGE_BYTES_MAX:
            return HttpResponseBadRequest("invalid image")
    else:
        for s in request.POST.getlist("img")[:IMAGES_MAX]:
            try:
                raw = base64.b64decode(s.split(",", 1)[-1], validate=True)
            except binascii.Error, ValueError:
                return HttpResponseBadRequest("invalid image")
            if not raw.startswith(b"\x89PNG") or len(raw) > IMAGE_BYTES_MAX:
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
    classic_crosspost = user.preference.mastodon_repost_mode == 1
    if classic_crosspost and user.mastodon:
        attrs = {"platform": "mastodon", "mode": "wrapped"}
        try:
            user.mastodon.post(
                comment,
                visibility,
                attachments=[
                    (f"{year}-{k + 1}.png", img, "image/png")
                    for k, img in enumerate(images)
                ],
            )
            sentry_count("crosspost.success", attributes=attrs)
        except Exception as e:
            logger.warning(f"wrapped post to {user.mastodon} failed: {e}")
            sentry_count("crosspost.failure", attributes=attrs)
    elif post and user.mastodon:
        assert post.url is not None
        user.mastodon.boost_later(post.url)
    if visibility == VisibilityType.Public and user.bluesky:
        o = EmbedObj("🧩", "", user.absolute_url)
        txt = comment.rstrip() + "\n\n##obj##"
        attrs = {"platform": "bluesky", "mode": "wrapped"}
        try:
            user.bluesky.post(txt, obj=o, images=images)
            sentry_count("crosspost.success", attributes=attrs)
        except Exception as e:
            logger.warning(f"wrapped post to {user.bluesky} failed: {e}")
            sentry_count("crosspost.failure", attributes=attrs)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"ok": True, "url": post.url if post else None})
    messages.add_message(request, messages.INFO, _("Summary posted to timeline."))
    return HttpResponseRedirect(reverse("journal:wrapped", args=[year]))


def _ref(item: Item) -> dict[str, Any]:
    return {
        "u": item.url,
        "t": item.display_title,
        "cov": item.display_cover_image_url,
        "c": str(item.category),
    }


@login_required
@require_GET
def wrapped_lookup(request: AuthedHttpRequest):
    """Resolve a pasted link for Make a card: found, fetching, unsupported, missing or busy."""
    url = request.GET.get("url", "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in url:
        return JsonResponse({"status": "invalid"})
    if FediverseInstance.is_local_item_url(url):
        item = Item.get_by_url(urlparse(url).path, resolve_merge=True)
        if not item or item.is_deleted:
            return JsonResponse({"status": "missing"})
        return JsonResponse({"status": "found", "item": _ref(item)})
    site = SiteManager.get_site_by_url(url, detect_redirection=False)
    if not site:
        return JsonResponse({"status": "unsupported"})
    item = site.get_item()
    if item and not item.is_deleted:
        return JsonResponse({"status": "found", "item": _ref(item)})
    if get_fetch_lock(request.user, url):
        enqueue_fetch(url, False, request.user)
        return JsonResponse({"status": "fetching"})
    return JsonResponse({"status": "busy"})


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


@require_GET
def wrapped_cover(request: HttpRequest, item_uuid: str):
    """A cover served from this site's origin, so a card drawn into a canvas can be exported.

    On an instance whose media lives on another host, a cross-origin image would taint the canvas
    and block the PNG and GIF download.
    """
    item = Item.get_by_url(item_uuid)
    if not item or item.is_deleted or not item.has_cover():
        raise Http404()
    try:
        f = item.cover.open("rb")
    except Exception:
        raise Http404()
    resp = FileResponse(
        f,
        content_type="image/jpeg"
        if item.cover.name.endswith((".jpg", ".jpeg"))
        else None,
    )
    resp["Cache-Control"] = "public, max-age=604800"
    return resp
