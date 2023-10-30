import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import AuthedHttpRequest, PageLinksGenerator, get_uuid_or_404
from mastodon.api import (
    get_spoiler_text,
    get_status_id_by_url,
    get_visibility,
    post_toot,
)
from takahe.utils import Takahe

from ..models import Comment, Mark, Piece, ShelfType, ShelfTypeNames, TagManager
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
    request.user.identity.shelf_manager.move_item(item, ShelfType.WISHLIST)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    return HttpResponse(_checkmark)


@login_required
def like(request: AuthedHttpRequest, piece_uuid):
    if request.method != "POST":
        raise BadRequest()
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece:
        raise Http404()
    post = piece.latest_post
    if post:
        Takahe.like_post(post.pk, request.user.identity.pk)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    elif request.GET.get("stats"):
        return render(
            request,
            "like_stats.html",
            {
                "piece": piece,
                "liked": True,
                "label": request.GET.get("label"),
                "icon": request.GET.get("icon"),
            },
        )
    return HttpResponse(_checkmark)


@login_required
def unlike(request: AuthedHttpRequest, piece_uuid):
    if request.method != "POST":
        raise BadRequest()
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece:
        raise Http404()
    post = piece.latest_post
    if post:
        Takahe.unlike_post(post.pk, request.user.identity.pk)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    elif request.GET.get("stats"):
        return render(
            request,
            "like_stats.html",
            {
                "piece": piece,
                "liked": False,
                "label": request.GET.get("label"),
                "icon": request.GET.get("icon"),
            },
        )
    return HttpResponse(_checkmark)


@login_required
def mark(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.method == "GET":
        tags = request.user.identity.tag_manager.get_item_tags(item)
        shelf_types = [
            (n[1], n[2]) for n in iter(ShelfTypeNames) if n[0] == item.category
        ]
        shelf_type = request.GET.get("shelf_type", mark.shelf_type)
        return render(
            request,
            "mark.html",
            {
                "item": item,
                "mark": mark,
                "shelf_type": shelf_type,
                "tags": ",".join(tags),
                "shelf_types": shelf_types,
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
            status = ShelfType(request.POST.get("status"))
            text = request.POST.get("text")
            tags = request.POST.get("tags")
            tags = tags.split(",") if tags else []
            share_to_mastodon = bool(
                request.POST.get("share_to_mastodon", default=False)
            )
            mark_date = None
            if request.POST.get("mark_anotherday"):
                dt = parse_datetime(request.POST.get("mark_date", "") + " 20:00:00")
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
            except PermissionDenied as e:
                _logger.warn(f"post to mastodon error 401 {request.user}")
                return render_relogin(request)
            except ValueError as e:
                _logger.warn(f"post to mastodon error {e} {request.user}")
                err = _("内容长度超出实例限制") if str(e) == "422" else str(e)
                return render(
                    request,
                    "common/error.html",
                    {
                        "msg": _("标记已保存，但是未能分享到联邦宇宙"),
                        "secondary_msg": err,
                    },
                )
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    raise BadRequest()


def share_comment(user, item, text, visibility, shared_link=None, position=None):
    post_error = False
    status_id = get_status_id_by_url(shared_link)
    link = (
        item.get_absolute_url_with_position(position) if position else item.absolute_url
    )
    action_label = "评论" if text else "分享"
    status = f"{action_label}{ItemCategory(item.category).label}《{item.display_title}》\n{link}\n\n{text}"
    spoiler, status = get_spoiler_text(status, item)
    try:
        response = post_toot(
            user.mastodon_site,
            status,
            get_visibility(visibility, user),
            user.mastodon_token,
            False,
            status_id,
            spoiler,
        )
        if response and response.status_code in [200, 201]:
            j = response.json()
            if "url" in j:
                shared_link = j["url"]
    except Exception as e:
        if settings.DEBUG:
            raise
        post_error = True
    return post_error, shared_link


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
    if not item.class_name in ["podcastepisode", "tvepisode"]:
        raise BadRequest("不支持评论此类型的条目")
    # episode = None
    # if item.class_name == "tvseason":
    #     try:
    #         episode = int(request.POST.get("episode", 0))
    #     except:
    #         episode = 0
    #     if episode <= 0:
    #         raise BadRequest("请输入正确的集数")
    comment = Comment.objects.filter(owner=request.user, item=item).first()
    if request.method == "GET":
        return render(
            request,
            f"comment.html",
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
            except:
                if settings.DEBUG:
                    raise
                position = None
        share_to_mastodon = bool(request.POST.get("share_to_mastodon", default=False))
        shared_link = comment.metadata.get("shared_link") if comment else None
        post_error = False
        if share_to_mastodon and request.user.mastodon_username:
            post_error, shared_link = share_comment(
                request.user, item, text, visibility, shared_link, position
            )
        Comment.objects.update_or_create(
            owner=request.user,
            item=item,
            # metadata__episode=episode,
            defaults={
                "text": text,
                "visibility": visibility,
                "metadata": {
                    "shared_link": shared_link,
                    "position": position,
                },
            },
        )

        # if comment:
        #     comment.visibility = visibility
        #     comment.text = text
        #     comment.metadata["position"] = position
        #     comment.metadata["episode"] = episode
        #     if shared_link:
        #         comment.metadata["shared_link"] = shared_link
        #     comment.save()
        # else:
        #     comment = Comment.objects.create(
        #         owner=request.user,
        #         item=item,
        #         text=text,
        #         visibility=visibility,
        #         metadata={
        #             "shared_link": shared_link,
        #             "position": position,
        #             "episode": episode,
        #         },
        #     )
        if post_error:
            return render_relogin(request)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    raise BadRequest()


def user_mark_list(
    request: AuthedHttpRequest, user_name, shelf_type, item_category, year=None
):
    return render_list(
        request,
        user_name,
        "mark",
        shelf_type=shelf_type,
        item_category=item_category,
        year=year,
    )
