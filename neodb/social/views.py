from enum import IntEnum
from typing import cast

from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item, ItemCategory, PodcastEpisode
from common.models import SiteConfig
from common.models.misc import int_
from common.validators import get_safe_referer_url
from journal.models import (
    CrosspostRetry,
    Piece,
    Rating,
    ShelfType,
    attach_reading_progress,
)
from journal.models.common import (
    prefetch_pieces_for_posts,
    q_owned_piece_visible_to_user,
)
from journal.search import JournalIndex, JournalQueryParser
from social.feed_grouping import FeedEvent, FeedEventGroup, group_feed_events
from takahe.models import Post, PostInteraction, TimelineEvent
from takahe.utils import Takahe
from users.models import APIdentity

PAGE_SIZE = 10
MAX_UNREAD_DISPLAY = 99
_all_notification_types = [
    "liked",
    "boosted",
    "mentioned",
    "followed",
    "follow_requested",
]
_urgent_notification_types = [
    "mentioned",
    "follow_requested",
]


class FeedType(IntEnum):
    """Which timeline the home feed shows. Values are part of the ``?typ=``
    query string of ``social:data``, so they must stay stable."""

    following = 0
    focus = 1  # marks and reviews from those you follow
    local = 2
    world = 3


# Public timelines are opt-in per site; the switch lives in Manage > Feed.
_PUBLIC_FEED_OPTIONS = {
    FeedType.local: "feed_show_local",
    FeedType.world: "feed_show_world",
}

_FEED_TITLES = {
    FeedType.following: _("Activities from those you follow"),
    FeedType.focus: _("Activities from those you follow"),
    FeedType.local: _("Activities on this site"),
    FeedType.world: _("Activities from the fediverse"),
}


def _feed_enabled(typ: int) -> bool:
    option = _PUBLIC_FEED_OPTIONS.get(cast(FeedType, typ))
    return getattr(SiteConfig.system, option) if option else True


def _public_posts(typ: int, identity: APIdentity) -> QuerySet[Post]:
    """Public posts for the local or world timeline, newest first.

    Ordered by id rather than published time, because the HTMX cursor pages
    with ``id__lt``; the two must agree or rows are skipped or repeated.
    """
    posts = (
        Post.objects.not_hidden()
        .public()
        .not_restricted()
        .exclude(author__discoverable=False)
        .not_blocked_by(identity.takahe_identity)
    )
    if typ == FeedType.local:
        posts = posts.filter(local=True)
    return posts.order_by("-id")


def _unread_count(user) -> str | int:
    """Unread notifications for the bell in the page header, capped for display."""
    unread_ids = list(
        Takahe.get_events(user.identity.pk, _all_notification_types)
        .filter(seen=False, dismissed=False)
        .values_list("pk", flat=True)[: MAX_UNREAD_DISPLAY + 1]
    )
    if len(unread_ids) > MAX_UNREAD_DISPLAY:
        return f"{MAX_UNREAD_DISPLAY}+"
    return len(unread_ids)


def _add_interaction_to_events(events, identity_id):
    interactions = PostInteraction.objects.filter(
        identity_id=identity_id,
        post_id__in=[event.subject_post_id for event in events],
        type__in=["like", "boost"],
        state__in=["new", "fanned_out"],
    ).values_list("post_id", "type")
    for event in events:
        if event.subject_post_id:
            event.subject_post.liked_by_current_user = (
                event.subject_post_id,
                "like",
            ) in interactions
            event.subject_post.boosted_by_current_user = (
                event.subject_post_id,
                "boost",
            ) in interactions


def _sidebar_context(identity: APIdentity) -> dict:
    """The tags and posts blocks of the sidebar, for the viewer's own feed.

    The profile page builds the same blocks for whoever is on display; here
    the viewer is always the owner, so nothing is hidden from them.
    """
    top_tags = identity.tag_manager.get_tags(public_only=False, pinned_only=True)[:10]
    if not top_tags.exists():
        top_tags = identity.tag_manager.get_tags(public_only=False)[:10]
    recent_posts = list(Takahe.get_recent_posts(identity.pk, identity.pk)[:10])
    prefetch_pieces_for_posts(recent_posts)
    return {"top_tags": top_tags, "recent_posts": recent_posts}


def _in_progress_context(identity: APIdentity) -> dict:
    """What the viewer is in the middle of. Pages showing it load the player."""
    podcast_ids = [
        member.item_id
        for member in identity.shelf_manager.get_latest_members(
            ShelfType.PROGRESS, ItemCategory.Podcast
        )
    ]
    recent_podcast_episodes = list(
        PodcastEpisode.objects.filter(program_id__in=podcast_ids)
        .select_related("program")
        .order_by("-pub_date")[:10]
    )
    book_members = list(
        identity.shelf_manager.get_latest_members(ShelfType.PROGRESS, ItemCategory.Book)
        .select_related("current_progress")
        # Cards skip the metadata JSON (EGGPLANT-1DX).
        .prefetch_related(
            "item",
            Item.external_resources_prefetch(lookup="item__external_resources"),
        )[:10]
    )
    attach_reading_progress(book_members)
    return {
        "recent_podcast_episodes": recent_podcast_episodes,
        "books_in_progress": [member.item for member in book_members],
    }


@require_http_methods(["GET"])
@login_required
def feed(request, typ=FeedType.following):
    if not _feed_enabled(typ):
        raise Http404
    user = request.user
    data = {"unread": _unread_count(user)}
    data["feed_type"] = typ
    data["feed_title"] = _FEED_TITLES.get(typ, _FEED_TITLES[FeedType.following])
    data["show_local_feed"] = SiteConfig.system.feed_show_local
    data["show_world_feed"] = SiteConfig.system.feed_show_world
    data.update(_sidebar_context(user.identity))
    data.update(_in_progress_context(user.identity))
    return render(request, "feed.html", data)


def focus(request):
    return feed(request, typ=FeedType.focus)


def local(request):
    return feed(request, typ=FeedType.local)


def world(request):
    return feed(request, typ=FeedType.world)


@require_http_methods(["GET"])
@login_required
def search(request):
    user = request.user
    data = {"unread": _unread_count(user)}
    return render(request, "search_feed.html", data)


@login_required
@require_http_methods(["GET"])
def search_data(request):
    identity_id = request.user.identity.pk
    page = int_(request.GET.get("lastpage")) + 1
    q = JournalQueryParser(request.GET.get("q", default=""), page, page_size=PAGE_SIZE)
    q.filter_by_owner(request.user.identity)
    q.filter("post_id", ">0")
    q.sort(["created:desc"])
    index = JournalIndex.instance()
    if q:
        r = index.search(q)
        events = [
            PostEvent(p)
            for p in r.posts.select_related("author", "preview_card")
            .prefetch_related("attachments", "mentions")
            .order_by("-id")
        ]
        _add_interaction_to_events(events, identity_id)
    else:
        events = []
    return render(
        request,
        "feed_events.html",
        {"events": events, "page": page},
    )


def _attach_group_ratings(grouped: list, viewing_user) -> None:
    """Give each mark group the author's own rating for its cover cards.

    A collapsed group shows the author's stars, like a profile shelf does, in
    place of the public average a single item card shows. All the ratings of a
    page are read in one query, filtered by what the viewer may see of each
    author's pieces: the local and world timelines show posts of users the
    viewer does not follow, so a followers-only rating on a public mark must
    stay hidden.
    """
    groups_by_owner: dict[int, list[FeedEventGroup]] = {}
    for event in grouped:
        if isinstance(event, FeedEventGroup) and event.owner_id:
            groups_by_owner.setdefault(event.owner_id, []).append(event)
    if not groups_by_owner:
        return
    query = None
    for owner in APIdentity.objects.filter(pk__in=groups_by_owner):
        item_ids = {
            item.pk for group in groups_by_owner[owner.pk] for item in group.items
        }
        if not item_ids:
            continue
        q = q_owned_piece_visible_to_user(viewing_user, owner) & Q(item_id__in=item_ids)
        query = q if query is None else query | q
    if query is None:
        return
    grades = {
        (owner_id, item_id): grade
        for owner_id, item_id, grade in Rating.objects.filter(query).values_list(
            "owner_id", "item_id", "grade"
        )
        if grade
    }
    for owner_id, groups in groups_by_owner.items():
        for group in groups:
            group.owner_rating_grades = {
                item.pk: grades[(owner_id, item.pk)]
                for item in group.items
                if (owner_id, item.pk) in grades
            }


def _public_data(request, typ: int, since_id: int, identity_id: int):
    posts = _public_posts(typ, request.user.identity)
    if since_id:
        posts = posts.filter(id__lt=since_id)
    post_list = list(
        posts.select_related(
            "author",
            "author__domain",
            "preview_card",
        ).prefetch_related("attachments", "mentions", "emojis")[:PAGE_SIZE]
    )
    events = [PostEvent(p) for p in post_list]
    _add_interaction_to_events(events, identity_id)
    prefetch_pieces_for_posts(post_list, request.user.identity)
    grouped = group_feed_events(cast(list[FeedEvent], events))
    _attach_group_ratings(grouped, request.user)
    return render(
        request,
        "feed_events.html",
        {"feed_type": typ, "events": grouped},
    )


@login_required
@require_http_methods(["GET"])
def data(request):
    since_id = int_(request.GET.get("last", 0))
    typ = int_(request.GET.get("typ", 0))
    if not _feed_enabled(typ):
        raise Http404
    identity_id = request.user.identity.pk
    if typ in _PUBLIC_FEED_OPTIONS:
        return _public_data(request, typ, since_id, identity_id)
    events = TimelineEvent.objects.filter(
        identity_id=identity_id,
        type__in=[TimelineEvent.Types.post, TimelineEvent.Types.boost],
        dismissed=False,
    )
    match typ:
        case 1:
            events = events.filter(
                subject_post__type_data__object__has_key="relatedWith"
            )
        case _:  # default: no replies
            events = events.filter(subject_post__in_reply_to__isnull=True)
    if since_id:
        events = events.filter(id__lt=since_id)
    events = list(
        events.select_related(
            "subject_post",
            "subject_post__author",
            "subject_post__author__domain",
            "subject_post__preview_card",
            "subject_identity",
            "subject_identity__domain",
            "subject_post_interaction",
            "subject_post_interaction__identity",
            "subject_post_interaction__identity__domain",
        )
        .prefetch_related(
            "subject_post__attachments",
            "subject_post__mentions",
            "subject_post__emojis",
        )
        .order_by("-id")[:PAGE_SIZE]
    )
    _add_interaction_to_events(events, identity_id)
    prefetch_pieces_for_posts(
        [e.subject_post for e in events if e.subject_post_id], request.user.identity
    )
    # events are TimelineEvent rows; the type checker can't see Django's implicit
    # id/_id attributes that FeedEvent declares, so assert the shape at this boundary.
    grouped = group_feed_events(cast(list[FeedEvent], events))
    _attach_group_ratings(grouped, request.user)
    return render(
        request,
        "feed_events.html",
        {"feed_type": typ, "events": grouped},
    )


@require_http_methods(["GET"])
@login_required
def notification(request):
    data = {"unread": _unread_count(request.user)}
    data.update(_in_progress_context(request.user.identity))
    return render(request, "notification.html", data)


@require_http_methods(["POST"])
@login_required
def dismiss_notification(request):
    Takahe.get_events(request.user.identity.pk, _all_notification_types).update(
        seen=True
    )
    referer = get_safe_referer_url(request, reverse("social:notification"))
    return redirect(referer)


class NotificationEvent:
    def __init__(self, tle) -> None:
        self.event = tle
        self.type = tle.type
        self.template = tle.type
        self.created = tle.created
        self.identity = APIdentity.from_takahe(tle.subject_identity)
        self.post = tle.subject_post
        self.seen = tle.seen
        if self.type == "mentioned":
            # for reply, self.post is the original post
            self.reply = self.post
            self.replies = [self.post]
            self.post = self.post.in_reply_to_post() if self.post else None
        self.piece = Piece.get_by_post_id(self.post.id) if self.post else None
        self.item = self.piece.item if hasattr(self.piece, "item") else None
        if self.piece and self.template in ["liked", "boosted", "mentioned"]:
            cls = self.piece.__class__.__name__.lower()
            self.template += "_" + cls


class PostEvent:
    """A bare post dressed up as a timeline event.

    Search results and the public timelines read posts directly, with no
    ``TimelineEvent`` row behind them. Duck-types the attributes that
    ``feed_events.html`` and ``social.feed_grouping`` expect, so both render
    through the same template as the home feed. ``pk`` is the post id, which
    is what the HTMX cursor pages on.
    """

    is_group = False

    def __init__(self, post: Post):
        self.type = "post"
        self.subject_post = post
        self.subject_post_id = post.id
        self.id = post.id
        self.pk = post.id
        self.created = post.created
        self.published = post.published
        self.identity = post.author
        self.subject_identity = post.author
        self.subject_identity_id = post.author_id


@login_required
@require_http_methods(["GET"])
def events(request):
    match request.GET.get("type"):
        case "follow":
            types = ["followed", "follow_requested"]
        case "mention":
            types = ["mentioned"]
        case _:
            types = _all_notification_types
    es = Takahe.get_events(request.user.identity.pk, types)
    last = request.GET.get("last")
    if last:
        # ignore malformed cursor values rather than 500 on the ORM cast
        last_dt = parse_datetime(last)
        if last_dt:
            es = es.filter(created__lt=last_dt)
    nes = [NotificationEvent(e) for e in es[:PAGE_SIZE]]
    return render(
        request,
        "events.html",
        {"events": nes},
    )


@login_required
@require_http_methods(["GET"])
def unread_notifications_status(request):
    if not request.user.is_authenticated:
        has_unread = False
        has_crosspost_failure = False
    else:
        has_unread = (
            Takahe.get_events(request.user.identity.pk, _all_notification_types)
            .filter(seen=False)
            .exists()
        )
        has_crosspost_failure = CrosspostRetry.objects.filter(
            user=request.user
        ).exists()
    return render(
        request,
        "notification_status.html",
        {
            "has_unread": has_unread,
            "has_crosspost_failure": has_crosspost_failure,
        },
    )
