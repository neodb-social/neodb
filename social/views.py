import logging
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, permission_required
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import BadRequest
from .models import *
from django.conf import settings
from management.models import Announcement


_logger = logging.getLogger(__name__)

PAGE_SIZE = 10


@login_required
def feed(request):
    if request.method != "GET":
        raise BadRequest()
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
def data(request):
    if request.method != "GET":
        raise BadRequest()
    return render(
        request,
        "feed_data.html",
        {
            "activities": ActivityManager(request.user).get_timeline(
                before_time=request.GET.get("last")
            )[:PAGE_SIZE],
        },
    )
