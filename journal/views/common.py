import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Min
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import (
    AuthedHttpRequest,
    PageLinksGenerator,
    get_uuid_or_404,
    target_identity_required,
)

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


@login_required
@target_identity_required
def render_list(
    request: AuthedHttpRequest,
    user_name,
    type,
    shelf_type: ShelfType | None = None,
    item_category=None,
    tag_title=None,
    year=None,
):
    target = request.target_identity
    viewer = request.user.identity
    tag = None
    if type == "mark" and shelf_type:
        queryset = target.shelf_manager.get_latest_members(shelf_type, item_category)
    elif type == "tagmember":
        tag = Tag.objects.filter(owner=target, title=tag_title).first()
        if not tag:
            return render_list_not_found(request)
        if tag.visibility != 0 and target != viewer:
            return render_list_not_found(request)
        queryset = TagMember.objects.filter(parent=tag)
    elif type == "review" and item_category:
        queryset = Review.objects.filter(q_item_in_category(item_category))
    else:
        raise BadRequest()
    start_date = queryset.aggregate(Min("created_time"))["created_time__min"]
    if start_date:
        start_year = start_date.year
        current_year = datetime.datetime.now().year
        years = reversed(range(start_year, current_year + 1))
    else:
        years = []
    queryset = queryset.filter(
        q_owned_piece_visible_to_user(request.user, target)
    ).order_by("-created_time")
    if year:
        year = int(year)
        queryset = queryset.filter(created_time__year=year)
    paginator = Paginator(queryset, PAGE_SIZE)
    page_number = int(request.GET.get("page", default=1))
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(PAGE_SIZE, page_number, paginator.num_pages)
    shelf_labels = get_shelf_labels_for_category(item_category) if item_category else []
    return render(
        request,
        f"user_{type}_list.html",
        {
            "user": target.user,
            "identity": target,
            "members": members,
            "tag": tag,
            "pagination": pagination,
            "years": years,
            "year": year,
            "shelf": shelf_type,
            "shelf_labels": shelf_labels,
            "category": item_category,
        },
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
