import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Min, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.utils import (
    AuthedHttpRequest,
    PageLinksGenerator,
    get_uuid_or_404,
    profile_identity_required,
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
            "msg": _("Data saved but unable to crosspost to Fediverse instance."),
            "secondary_msg": _(
                "Redirecting to your Fediverse instance now to re-authenticate."
            ),
        },
    )


def render_list_not_found(request):
    msg = _("List not found.")
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
    sort="time",
):
    target = request.target_identity
    viewer = request.user.identity
    tag = None
    sort = request.GET.get("sort")
    year = request.GET.get("year")
    if type == "mark" and shelf_type:
        queryset = target.shelf_manager.get_members(shelf_type, item_category)
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
        raise BadRequest(_("Invalid parameter"))
    if sort == "rating":
        rating = Rating.objects.filter(
            owner_id=OuterRef("owner_id"), item_id=OuterRef("item_id")
        )
        queryset = queryset.alias(
            rating_grade=Subquery(rating.values("grade"))
        ).order_by(F("rating_grade").desc(nulls_last=True))
    else:
        queryset = queryset.order_by("-created_time")
    start_date = queryset.aggregate(Min("created_time"))["created_time__min"]
    if start_date:
        start_year = start_date.year
        current_year = datetime.datetime.now().year
        years = reversed(range(start_year, current_year + 1))
    else:
        years = []
    queryset = queryset.filter(q_owned_piece_visible_to_user(request.user, target))
    if year:
        year = int(year)
        queryset = queryset.filter(created_time__year=year)
    paginator = Paginator(queryset, PAGE_SIZE)  # type:ignore
    page_number = int(request.GET.get("page", default=1))
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    shelf_labels = (
        ShelfManager.get_labels_for_category(item_category) if item_category else []
    )
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
            "sort": sort,
            "shelf": shelf_type,
            "shelf_labels": shelf_labels,
            "category": item_category,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def piece_delete(request, piece_uuid):
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    return_url = request.GET.get("return_url", None) or "/"
    if not piece.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        return render(
            request, "piece_delete.html", {"piece": piece, "return_url": return_url}
        )
    else:
        piece.delete()
        return redirect(return_url)
