import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import AuthedHttpRequest, get_uuid_or_404
from mastodon.api import boost_toot_later, share_comment
from takahe.utils import Takahe

from ..models import Comment, Mark, ShelfManager, ShelfType, TagManager
from .common import render_list, render_relogin, target_identity_required

_logger = logging.getLogger(__name__)
PAGE_SIZE = 10

_checkmark = "✔️".encode("utf-8")


@login_required
def wish(request: AuthedHttpRequest, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    mark = Mark(request.user.identity, item)
    if not mark.shelf_type:
        mark.update(ShelfType.WISHLIST)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    return HttpResponse(_checkmark)


@login_required
def mark(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.method == "GET":
        tags = request.user.identity.tag_manager.get_item_tags(item)
        shelf_actions = ShelfManager.get_actions_for_category(item.category)
        shelf_type = request.GET.get("shelf_type", mark.shelf_type)
        return render(
            request,
            "mark.html",
            {
                "item": item,
                "mark": mark,
                "shelf_type": shelf_type,
                "tags": ",".join(tags),
                "shelf_actions": shelf_actions,
                "date_today": timezone.localdate().isoformat(),
            },
        )
    elif request.method == "POST":
        if request.POST.get("delete", default=False):
            mark.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
        else:
            visibility = int(request.POST.get("visibility", default=0))
            rating_grade = request.POST.get("rating_grade", default=0)
            rating_grade = int(rating_grade) if rating_grade else None
            status = ShelfType(request.POST.get("status", "wishlist"))
            text = request.POST.get("text")
            tags = request.POST.get("tags")
            tags = tags.split(",") if tags else []
            share_to_mastodon = bool(
                request.POST.get("share_to_mastodon", default=False)
            )
            mark_date = None
            if request.POST.get("mark_anotherday"):
                shelf_time_offset = {
                    ShelfType.WISHLIST: " 20:00:00",
                    ShelfType.PROGRESS: " 21:00:00",
                    ShelfType.DROPPED: " 21:30:00",
                    ShelfType.COMPLETE: " 22:00:00",
                }
                dt = parse_datetime(
                    request.POST.get("mark_date", "")
                    + shelf_time_offset.get(status, "")
                )
                mark_date = (
                    dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
                )
                if mark_date and mark_date >= timezone.now():
                    mark_date = None
            TagManager.tag_item(item, request.user.identity, tags, visibility)
            try:
                mark.update(
                    status,
                    text,
                    rating_grade,
                    visibility,
                    share_to_mastodon=share_to_mastodon,
                    created_time=mark_date,
                )
            except PermissionDenied:
                _logger.warn(f"post to mastodon error 401 {request.user}")
                return render_relogin(request)
            except ValueError as e:
                _logger.warn(f"post to mastodon error {e} {request.user}")
                err = (
                    _("Content too long for your Mastodon instance.")
                    if str(e) == "422"
                    else str(e)
                )
                return render(
                    request,
                    "common/error.html",
                    {
                        "msg": _("Data saved but unable to repost to Fediverse."),
                        "secondary_msg": err,
                    },
                )
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    raise BadRequest()


@login_required
def mark_log(request: AuthedHttpRequest, item_uuid, log_id):
    """
    Delete log of one item by log id.
    """
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.method == "POST":
        if request.GET.get("delete", default=False):
            if log_id:
                mark.delete_log(log_id)
            else:
                mark.delete_all_logs()
            return render(request, "_item_user_mark_history.html", {"mark": mark})
    raise BadRequest()


@login_required
def comment(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name not in ["podcastepisode", "tvepisode"]:
        raise BadRequest("Commenting this type of items is not supported yet.")
    comment = Comment.objects.filter(owner=request.user.identity, item=item).first()
    if request.method == "GET":
        return render(
            request,
            "comment.html",
            {
                "item": item,
                "comment": comment,
            },
        )
    elif request.method == "POST":
        if request.POST.get("delete", default=False):
            if not comment:
                raise Http404()
            comment.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
        visibility = int(request.POST.get("visibility", default=0))
        text = request.POST.get("text")
        position = None
        if item.class_name == "podcastepisode":
            position = request.POST.get("position") or "0:0:0"
            try:
                pos = datetime.strptime(position, "%H:%M:%S")
                position = pos.hour * 3600 + pos.minute * 60 + pos.second
            except Exception:
                if settings.DEBUG:
                    raise
                position = None
        d = {"text": text, "visibility": visibility}
        if position:
            d["metadata"] = {"position": position}
        comment, _ = Comment.objects.update_or_create(
            owner=request.user.identity, item=item, defaults=d
        )
        post = Takahe.post_comment(comment, False)
        share_to_mastodon = bool(request.POST.get("share_to_mastodon", default=False))
        if post and share_to_mastodon:
            if request.user.preference.mastodon_repost_mode == 1:
                share_comment(comment)
            else:
                boost_toot_later(request.user, post.url)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    raise BadRequest()


def user_mark_list(request: AuthedHttpRequest, user_name, shelf_type, item_category):
    return render_list(
        request, user_name, "mark", shelf_type=shelf_type, item_category=item_category
    )
