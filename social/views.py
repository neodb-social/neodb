import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from journal.models import *
from takahe.utils import Takahe

from .models import *

PAGE_SIZE = 10


@require_http_methods(["GET"])
@login_required
def feed(request):
    if not request.user.registration_complete:
        return redirect(reverse("users:register"))
    user = request.user
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
    return render(
        request,
        "feed.html",
        {
            "recent_podcast_episodes": recent_podcast_episodes,
            "books_in_progress": books_in_progress,
            "tvshows_in_progress": tvshows_in_progress,
        },
    )


@login_required
@require_http_methods(["GET"])
def data(request):
    return render(
        request,
        "feed_data.html",
        {
            "activities": ActivityManager(request.user.identity).get_timeline(
                before_time=request.GET.get("last")
            )[:PAGE_SIZE],
        },
    )


@require_http_methods(["GET"])
@login_required
def notification(request):
    if not request.user.registration_complete:
        return redirect(reverse("users:register"))
    user = request.user
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
    return render(
        request,
        "notification.html",
        {
            "recent_podcast_episodes": recent_podcast_episodes,
            "books_in_progress": books_in_progress,
            "tvshows_in_progress": tvshows_in_progress,
        },
    )


class NotificationEvent:
    def __init__(self, tle) -> None:
        self.event = tle
        self.type = tle.type
        self.template = tle.type
        self.created = tle.created
        self.identity = (
            APIdentity.objects.filter(pk=tle.subject_identity.pk).first()
            if tle.subject_identity
            else None
        )
        self.post = tle.subject_post
        if self.type == "mentioned":
            # for reply, self.post is the original post
            self.reply = self.post
            self.replies = [self.post]
            self.post = self.post.in_reply_to_post() if self.post else None
        self.piece = Piece.get_by_post_id(self.post.id) if self.post else None
        self.item = getattr(self.piece, "item") if hasattr(self.piece, "item") else None
        if self.piece and self.template in ["liked", "boost", "mentioned"]:
            cls = self.piece.__class__.__name__.lower()
            self.template += "_" + cls


@login_required
@require_http_methods(["GET"])
def events(request):
    es = Takahe.get_events(request.user.identity.pk)
    last = request.GET.get("last")
    if last:
        es = es.filter(created__lt=last)
    nes = [NotificationEvent(e) for e in es[:PAGE_SIZE]]
    return render(
        request,
        "events.html",
        {"events": nes},
    )
