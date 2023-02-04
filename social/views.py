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
    unread = Announcement.objects.filter(pk__gt=user.read_announcement_index).order_by(
        "-pk"
    )
    if unread:
        user.read_announcement_index = Announcement.objects.latest("pk").pk
        user.save(update_fields=["read_announcement_index"])
    return render(
        request,
        "feed.html",
        {
            "top_tags": user.tag_manager.all_tags[:10],
            "unread_announcements": unread,
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
