import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.utils import AuthedHttpRequest

from ..forms import *
from ..models import *
from .common import profile_identity_required, target_identity_required


@require_http_methods(["GET"])
@profile_identity_required
def profile(request: AuthedHttpRequest, user_name):
    target = request.target_identity

    if not request.user.is_authenticated and (
        not target.local or not target.anonymous_viewable
    ):
        return render(
            request,
            "users/home_anonymous.html",
            {"identity": target, "redir": f"/account/login?next={target.url}"},
        )

    if (target.local and user_name != target.handle) or (
        not target.local and user_name != f"@{target.handle}"
    ):
        return render(
            request,
            "users/home_anonymous.html",
            {"identity": target, "redir": target.url},
        )

    me = target.local and target.user == request.user

    qv = q_owned_piece_visible_to_user(request.user, target)
    shelf_list = {}
    visbile_categories = [
        ItemCategory.Book,
        ItemCategory.Movie,
        ItemCategory.TV,
        ItemCategory.Music,
        ItemCategory.Podcast,
        ItemCategory.Game,
        ItemCategory.Performance,
    ]
    for category in visbile_categories:
        shelf_list[category] = {}
        for shelf_type in ShelfType:
            if shelf_type == ShelfType.DROPPED:
                continue
            label = target.shelf_manager.get_label(shelf_type, category)
            if label is not None:
                members = target.shelf_manager.get_latest_members(
                    shelf_type, category
                ).filter(qv)
                shelf_list[category][shelf_type] = {
                    "title": label,
                    "count": members.count(),
                    "members": members[:10].prefetch_related("item"),
                }
        reviews = (
            Review.objects.filter(q_item_in_category(category))
            .filter(qv)
            .order_by("-created_time")
        )
        shelf_list[category]["reviewed"] = {
            "title": _("{shelf_label} {item_category}").format(
                shelf_label="reviewed", item_category=category.label
            ),
            "count": reviews.count(),
            "members": reviews[:10].prefetch_related("item"),
        }
    collections = Collection.objects.filter(qv).order_by("-created_time")
    liked_collections = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    ).order_by("-edited_time")
    if not me:
        liked_collections = liked_collections.filter(
            q_piece_visible_to_user(request.user)
        )
        top_tags = target.tag_manager.public_tags[:10]
        year = None
    else:
        top_tags = target.tag_manager.all_tags[:10]
        today = datetime.date.today()
        if today.month > 11:
            year = today.year
        elif today.month < 2:
            year = today.year - 1
        else:
            year = None
    return render(
        request,
        "profile.html",
        {
            "user": target.user,
            "identity": target,
            "me": me,
            "top_tags": top_tags,
            "shelf_list": shelf_list,
            "collections": collections[:10],
            "collections_count": collections.count(),
            "liked_collections": liked_collections[:10],
            "liked_collections_count": liked_collections.count(),
            "layout": target.preference.profile_layout,
            "year": year,
        },
    )


@require_http_methods(["GET"])
@login_required
@target_identity_required
def user_calendar_data(request, user_name):
    target = request.target_identity
    max_visiblity = max_visiblity_to_user(request.user, target)
    calendar_data = target.shelf_manager.get_calendar_data(max_visiblity)
    return render(
        request,
        "calendar_data.html",
        {
            "calendar_data": calendar_data,
        },
    )
