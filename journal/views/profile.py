from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from user_messages import api as msg

from catalog.models import *
from users.models import User
from users.views import render_user_blocked, render_user_not_found

from ..forms import *
from ..models import *
from .common import render_list


def profile(request, user_name):
    if request.method != "GET":
        raise BadRequest()
    user = User.get(user_name, case_sensitive=True)
    if user is None or not user.is_active:
        return render_user_not_found(request)
    if user.mastodon_acct != user_name and user.username != user_name:
        return redirect(user.url)
    if not request.user.is_authenticated and user.preference.no_anonymous_view:
        return render(request, "users/home_anonymous.html", {"user": user})
    if user != request.user and (
        user.is_blocked_by(request.user) or user.is_blocking(request.user)
    ):
        return render_user_blocked(request)

    qv = q_visible_to(request.user, user)
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
            label = user.shelf_manager.get_label(shelf_type, category)
            if label is not None:
                members = user.shelf_manager.get_latest_members(
                    shelf_type, category
                ).filter(qv)
                shelf_list[category][shelf_type] = {
                    "title": label,
                    "count": members.count(),
                    "members": members[:10].prefetch_related("item"),
                }
        reviews = (
            Review.objects.filter(owner=user)
            .filter(qv)
            .filter(query_item_category(category))
            .order_by("-created_time")
        )
        shelf_list[category]["reviewed"] = {
            "title": "评论过的" + category.label,
            "count": reviews.count(),
            "members": reviews[:10].prefetch_related("item"),
        }
    collections = (
        Collection.objects.filter(owner=user).filter(qv).order_by("-created_time")
    )
    liked_collections = (
        Like.user_likes_by_class(user, Collection)
        .order_by("-edited_time")
        .values_list("target_id", flat=True)
    )
    if user != request.user:
        liked_collections = liked_collections.filter(query_visible(request.user))
        top_tags = user.tag_manager.public_tags[:10]
    else:
        top_tags = user.tag_manager.all_tags[:10]
    return render(
        request,
        "profile.html",
        {
            "user": user,
            "top_tags": top_tags,
            "shelf_list": shelf_list,
            "collections": collections[:10],
            "collections_count": collections.count(),
            "liked_collections": [
                Collection.objects.get(id=i)
                for i in liked_collections.order_by("-edited_time")[:10]
            ],
            "liked_collections_count": liked_collections.count(),
            "layout": user.preference.profile_layout,
        },
    )


def user_calendar_data(request, user_name):
    if request.method != "GET":
        raise BadRequest()
    user = User.get(user_name)
    if user is None or not request.user.is_authenticated:
        return HttpResponse("")
    max_visiblity = max_visiblity_to(request.user, user)
    calendar_data = user.shelf_manager.get_calendar_data(max_visiblity)
    return render(
        request,
        "calendar_data.html",
        {
            "calendar_data": calendar_data,
        },
    )
