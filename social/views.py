from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalog.models import Edition, Item, ItemCategory, PodcastEpisode
from common.models.misc import int_
from journal.models import Piece, ShelfType
from journal.search import JournalIndex, JournalQueryParser
from takahe.models import Post, PostInteraction, TimelineEvent
from takahe.utils import Takahe
from users.models import APIdentity

PAGE_SIZE = 10
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


def _sidebar_context(user):
    podcast_ids = [
        p.item_id
        for p in user.shelf_manager.get_latest_members(
            ShelfType.PROGRESS, ItemCategory.Podcast
        )
    ]
    recent_podcast_episodes = PodcastEpisode.objects.filter(
        program_id__in=podcast_ids
    ).order_by("-pub_date")[:10]
    books_in_progress = Edition.objects.filter(
        id__in=[
            p.item_id
            for p in user.shelf_manager.get_latest_members(
                ShelfType.PROGRESS, ItemCategory.Book
            )[:10]
        ]
    )
    tvshows_in_progress = Item.objects.filter(
        id__in=[
            p.item_id
            for p in user.shelf_manager.get_latest_members(
                ShelfType.PROGRESS, ItemCategory.TV
            )[:10]
        ]
    )
    unread = (
        Takahe.get_events(user.identity.pk, _all_notification_types)
        .filter(seen=False)
        .count()
    )
    return {
        "unread": unread,
        "recent_podcast_episodes": recent_podcast_episodes,
        "books_in_progress": books_in_progress,
        "tvshows_in_progress": tvshows_in_progress,
    }


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


@require_http_methods(["GET"])
@login_required
def feed(request, typ=0):
    user = request.user
    data = _sidebar_context(user)
    data["feed_type"] = typ
    return render(request, "feed.html", data)


def focus(request):
    return feed(request, typ=1)


@require_http_methods(["GET"])
@login_required
def search(request):
    user = request.user
    data = _sidebar_context(user)
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
            SearchResultEvent(p)
            for p in r.posts.select_related("author")
            .prefetch_related("attachments")
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


@login_required
@require_http_methods(["GET"])
def data(request):
    since_id = int_(request.GET.get("last", 0))
    typ = int_(request.GET.get("typ", 0))
    identity_id = request.user.identity.pk
    events = TimelineEvent.objects.filter(
        identity_id=identity_id,
        type__in=[TimelineEvent.Types.post, TimelineEvent.Types.boost],
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
            # "subject_post__author__domain",
            "subject_identity",
            # "subject_identity__domain",
            "subject_post_interaction",
            "subject_post_interaction__identity",
            # "subject_post_interaction__identity__domain",
        )
        .prefetch_related(
            "subject_post__attachments",
            # "subject_post__mentions",
            # "subject_post__emojis",
        )
        .order_by("-id")[:PAGE_SIZE]
    )
    _add_interaction_to_events(events, identity_id)
    return render(
        request,
        "feed_events.html",
        {"feed_type": typ, "events": events},
    )


@require_http_methods(["GET"])
@login_required
def notification(request):
    return render(request, "notification.html", _sidebar_context(request.user))


@require_http_methods(["POST"])
@login_required
def dismiss_notification(request):
    Takahe.get_events(request.user.identity.pk, _all_notification_types).update(
        seen=True
    )
    return redirect(request.META.get("HTTP_REFERER", reverse("social:notification")))


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
        self.item = getattr(self.piece, "item") if hasattr(self.piece, "item") else None
        if self.piece and self.template in ["liked", "boosted", "mentioned"]:
            cls = self.piece.__class__.__name__.lower()
            self.template += "_" + cls


class SearchResultEvent:
    def __init__(self, post: Post):
        self.type = "post"
        self.subject_post = post
        self.subject_post_id = post.id
        self.created = post.created
        self.published = post.published
        self.identity = post.author


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
        es = es.filter(created__lt=last)
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
    else:
        has_unread = (
            Takahe.get_events(request.user.identity.pk, _all_notification_types)
            .filter(seen=False)
            .exists()
        )
    return render(request, "notification_status.html", {"has_unread": has_unread})
