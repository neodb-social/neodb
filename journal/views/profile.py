import datetime
from urllib.parse import quote_plus

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.utils import (
    AuthedHttpRequest,
    get_uuid_or_404,
    profile_identity_required,
    target_identity_required,
)
from takahe.utils import Takahe

from ..forms import *
from ..models import *


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def group(request: AuthedHttpRequest, user_name):
    target = request.target_identity
    if not target.is_group:
        return redirect("journal:user_profile", user_name=user_name)
    if request.method == "HEAD":
        return HttpResponse()
    viewer_pk = request.user.identity.pk if request.user.is_authenticated else None
    boosts = Takahe.get_events(target.pk, ["boost"], False)
    recent_posts = Takahe.get_recent_posts(target.pk, viewer_pk)[:10]
    return render(
        request,
        "group.html",
        {
            "user": target.user,
            "identity": target,
            "events": boosts[:20],
            "recent_posts": recent_posts,
        },
    )


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def profile(request: AuthedHttpRequest, user_name):
    target = request.target_identity
    if target.is_group:
        return redirect("journal:group_profile", user_name=user_name)
    if request.method == "HEAD":
        return HttpResponse()
    anonymous = not request.user.is_authenticated
    if anonymous and (not target.local or not target.anonymous_viewable):
        return render(
            request,
            "users/home_anonymous.html",
            {
                "identity": target,
                "redir": f"/account/login?next={quote_plus(target.url)}",
            },
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
    stats = target.shelf_manager.get_stats()
    for category in visbile_categories:
        shelf_list[category] = {}
        for shelf_type in ShelfType:
            if shelf_type == ShelfType.DROPPED:
                continue
            label = target.shelf_manager.get_label(shelf_type, category)
            if label:
                members = target.shelf_manager.get_latest_members(
                    shelf_type, category
                ).filter(qv)
                shelf_list[category][shelf_type] = {
                    "title": label,
                    "count": stats[category][shelf_type],
                    "members": members[:10].prefetch_related("item"),
                }
        reviews = (
            Review.objects.filter(q_item_in_category(category))
            .filter(qv)
            .order_by("-created_time")
        )
        shelf_list[category]["reviewed"] = {
            "title": target.shelf_manager.get_label("reviewed", category),
            "count": stats[category].get("reviewed", 0),
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
        year = None
    else:
        today = datetime.date.today()
        if today.month >= 11:
            year = today.year
        elif today.month < 2:
            year = today.year - 1
        else:
            year = None
    top_tags = target.tag_manager.get_tags(public_only=not me, pinned_only=True)[:10]
    if not top_tags.exists():
        top_tags = target.tag_manager.get_tags(public_only=not me)[:10]
    if anonymous:
        recent_posts = None
    else:
        recent_posts = Takahe.get_recent_posts(target.pk, request.user.identity.pk)[:10]
    pinned_collections = Collection.objects.filter(
        interactions__interaction_type="pin", interactions__identity=target
    ).filter(qv)
    return render(
        request,
        "profile.html",
        {
            "user": target.user,
            "identity": target,
            "me": me,
            "top_tags": top_tags,
            "recent_posts": recent_posts,
            "shelf_list": shelf_list,
            "collections": collections[:10],
            "collections_count": collections.count(),
            "pinned_collections": pinned_collections[:10],
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


def profile_items(request: AuthedHttpRequest):
    collection_uuid = request.GET.get("collection")
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))

    items = []
    total = 0
    if collection.is_dynamic:
        viewer = request.user.identity if request.user.is_authenticated else None
        q = collection.get_query(viewer, page=1)
        if q:
            r = JournalIndex.instance().search(q)
            items = r.items
            total = r.total
    else:
        items = collection.ordered_items[:20]
        total = collection.members.count()

    return render(
        request,
        "profile_items.html",
        {
            "title": collection.title,
            "url": collection.url,
            "items": items,
            "total": total,
        },
    )
