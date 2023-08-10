from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import PageLinksGenerator, get_uuid_or_404
from users.models import User
from users.views import render_user_blocked, render_user_not_found

from ..forms import *
from ..models import *

PAGE_SIZE = 10


def render_relogin(request):
    return render(
        request,
        "common/error.html",
        {
            "url": reverse("users:connect") + "?domain=" + request.user.mastodon_site,
            "msg": _("信息已保存，但是未能分享到联邦宇宙"),
            "secondary_msg": _(
                "可能是你在联邦宇宙(Mastodon/Pleroma/...)的登录状态过期了，正在跳转到联邦宇宙重新登录😼"
            ),
        },
    )


def render_list_not_found(request):
    msg = _("相关列表不存在")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


def render_list(
    request, user_name, type, shelf_type=None, item_category=None, tag_title=None
):
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if user != request.user and (
        request.user.is_blocked_by(user) or request.user.is_blocking(user)
    ):
        return render_user_blocked(request)
    tag = None
    if type == "mark":
        queryset = user.shelf_manager.get_latest_members(shelf_type, item_category)
    elif type == "tagmember":
        tag = Tag.objects.filter(owner=user, title=tag_title).first()
        if not tag:
            return render_list_not_found(request)
        if tag.visibility != 0 and user != request.user:
            return render_list_not_found(request)
        queryset = TagMember.objects.filter(parent=tag)
    elif type == "review":
        queryset = Review.objects.filter(owner=user)
        queryset = queryset.filter(query_item_category(item_category))
    else:
        raise BadRequest()
    queryset = queryset.filter(q_visible_to(request.user, user)).order_by(
        "-created_time"
    )
    paginator = Paginator(queryset, PAGE_SIZE)
    page_number = request.GET.get("page", default=1)
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(PAGE_SIZE, page_number, paginator.num_pages)
    return render(
        request,
        f"user_{type}_list.html",
        {"user": user, "members": members, "tag": tag, "pagination": pagination},
    )


@login_required
def piece_delete(request, piece_uuid):
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    return_url = request.GET.get("return_url", None) or "/"
    if not piece.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        return render(
            request, "piece_delete.html", {"piece": piece, "return_url": return_url}
        )
    elif request.method == "POST":
        piece.delete()
        return redirect(return_url)
    else:
        raise BadRequest()
