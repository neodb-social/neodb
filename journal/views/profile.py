from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from user_messages import api as msg

from catalog.models import *
from common.utils import AuthedHttpRequest
from users.models import APIdentity, User
from users.views import render_user_blocked, render_user_not_found

from ..forms import *
from ..models import *
from .common import render_list, target_identity_required


@target_identity_required
def profile(request: AuthedHttpRequest, user_name):
    if request.method != "GET":
        raise BadRequest()
    target = request.target_identity
    # if user.mastodon_acct != user_name and user.username != user_name:
    #     return redirect(user.url)
    if not request.user.is_authenticated and target.preference.no_anonymous_view:
        return render(request, "users/home_anonymous.html", {"user": target.user})
    me = target.user == request.user

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
            "title": "评论过的" + category.label,
            "count": reviews.count(),
            "members": reviews[:10].prefetch_related("item"),
        }
    collections = Collection.objects.filter(qv).order_by("-created_time")
    liked_collections = (
        Like.user_likes_by_class(target, Collection)
        .order_by("-edited_time")
        .values_list("target_id", flat=True)
    )
    if not me:
        liked_collections = liked_collections.filter(
            q_piece_visible_to_user(request.user)
        )
        top_tags = target.tag_manager.public_tags[:10]
    else:
        top_tags = target.tag_manager.all_tags[:10]
    return render(
        request,
        "profile.html",
        {
            "user": target.user,
            "identity": target,
            "top_tags": top_tags,
            "shelf_list": shelf_list,
            "collections": collections[:10],
            "collections_count": collections.count(),
            "liked_collections": [
                Collection.objects.get(id=i)
                for i in liked_collections.order_by("-edited_time")[:10]
            ],
            "liked_collections_count": liked_collections.count(),
            "layout": target.preference.profile_layout,
        },
    )


def user_calendar_data(request, user_name):
    if request.method != "GET" or not request.user.is_authenticated:
        raise BadRequest()
    try:
        target = APIdentity.get_by_handler(user_name)
    except:
        return HttpResponse("unavailable")
    max_visiblity = max_visiblity_to_user(request.user, target)
    calendar_data = target.shelf_manager.get_calendar_data(max_visiblity)
    return render(
        request,
        "calendar_data.html",
        {
            "calendar_data": calendar_data,
        },
    )
