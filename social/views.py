import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from journal.models import *

from .models import *

_logger = logging.getLogger(__name__)

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
