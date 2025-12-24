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
from users.models import APIdentity

from ..forms import *
from ..models import *
from ..search import JournalIndex


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

    # backward compatible with legacy url format /users/linked_handle@remote/
    if not target.local and "@" in user_name and not user_name.startswith("@"):
        try:
            target = APIdentity.get_by_linked_handle(user_name)
        except APIdentity.DoesNotExist:
            pass

    # profile url must be either /users/local_handle/ or /users/@handle@remote/
    # if not, let's redirect it with meta in head
    if (target.local and user_name != target.handle) or (
        not target.local and user_name != f"@{target.handle}"
    ):
        return render(
            request,
            "users/home_anonymous.html",
            {"identity": target, "redir": target.url},
        )

    # anonymous user should not see real content unless permitted by user
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

    me = target.local and target.user == request.user

    qv = q_owned_piece_visible_to_user(request.user, target)
    default_layout = [{"id": "calendar_grid", "visibility": True}]
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
            default_layout.append(
                {"id": f"{category}_{shelf_type}", "visibility": True}
            )
            label = target.shelf_manager.get_label(shelf_type, category)
            if label:
                shelf_list[category][shelf_type] = {
                    "title": label,
                    "count": stats[category][shelf_type],
                }
        reviewed_label = target.shelf_manager.get_label("reviewed", category)
        if reviewed_label:
            default_layout.append({"id": f"{category}_reviewed", "visibility": True})
            shelf_list[category]["reviewed"] = {
                "title": reviewed_label,
                "count": stats[category].get("reviewed", 0),
            }
    collections_count = Collection.objects.filter(qv).count()
    liked_collections_queryset = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    ).order_by("-edited_time")
    if not me:
        liked_collections_queryset = liked_collections_queryset.filter(
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
    liked_collections_count = liked_collections_queryset.count()
    top_tags = target.tag_manager.get_tags(public_only=not me, pinned_only=True)[:10]
    if not top_tags.exists():
        top_tags = target.tag_manager.get_tags(public_only=not me)[:10]
    recent_posts = Takahe.get_recent_posts(
        target.pk, None if anonymous else request.user.identity.pk
    )[:10]
    default_layout.append({"id": "collection_created", "visibility": True})
    default_layout.append({"id": "collection_marked", "visibility": True})
    pinned_collections = (
        Collection.objects.filter(
            interactions__interaction_type="pin", interactions__identity=target
        )
        .order_by("-interactions__created_time")
        .filter(qv)[:10]
    )
    default_layout[0:0] = [
        {"id": f"collection_{collection.uuid}", "visibility": True}
        for collection in pinned_collections
    ]
    layout = target.preference.profile_layout or default_layout
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
            "collections_count": collections_count,
            "pinned_collections": pinned_collections,
            "liked_collections_count": liked_collections_count,
            "layout": layout,
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


@require_http_methods(["GET"])
def profile_collection_items(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        # raise PermissionDenied(_("Insufficient permission"))
        return HttpResponse()

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


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def profile_created_collections(request: AuthedHttpRequest, user_name):
    """
    Display created collections for a user profile page.
    """
    target = request.target_identity
    if not request.user.is_authenticated and not target.anonymous_viewable:
        # raise PermissionDenied(_("Login required"))
        return HttpResponse()

    # Get visibility filter
    qv = q_owned_piece_visible_to_user(request.user, target)

    # Get collections
    collections = Collection.objects.filter(qv).order_by("-created_time")[:20]
    total = Collection.objects.filter(qv).count()

    return render(
        request,
        "profile_items.html",
        {
            "title": _("collection"),
            "url": f"{target.url}collections/",
            "items": collections,
            "total": total,
            "show_create_button": target.user == request.user,
        },
    )


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def profile_liked_collections(request: AuthedHttpRequest, user_name):
    """
    Display liked collections for a user profile page.
    """
    target = request.target_identity
    if not request.user.is_authenticated and not target.anonymous_viewable:
        # raise PermissionDenied(_("Login required"))
        return HttpResponse()

    me = target.local and target.user == request.user

    # Get liked collections
    liked_collections = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    ).order_by("-edited_time")

    if not me:
        liked_collections = liked_collections.filter(
            q_piece_visible_to_user(request.user)
        )

    collections = liked_collections[:20]
    total = liked_collections.count()

    return render(
        request,
        "profile_items.html",
        {
            "title": _("liked collection"),
            "url": f"{target.url}like/collections/",
            "items": collections,
            "total": total,
        },
    )


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def profile_shelf_items(request: AuthedHttpRequest, user_name, category, shelf_type):
    """
    Display shelf items for a specific category and shelf type on profile pages.
    """
    target = request.target_identity
    if not request.user.is_authenticated and not target.anonymous_viewable:
        raise PermissionDenied(_("Login required"))

    # Validate category
    try:
        item_category = ItemCategory(category)
    except ValueError:
        # raise Http404(_("Invalid category"))
        return HttpResponse()

    # Validate shelf_type
    if shelf_type not in ShelfType.values and shelf_type != "reviewed":
        # raise Http404(_("Invalid shelf type"))
        return HttpResponse()

    # Get visibility filter
    qv = q_owned_piece_visible_to_user(request.user, target)

    # Get shelf label and URL
    if shelf_type == "reviewed":
        label = target.shelf_manager.get_label("reviewed", item_category)
        url = f"{target.url}reviewed/{category}/"
        # Get reviews for this category
        items_queryset = (
            Review.objects.filter(q_item_in_category(item_category))
            .filter(qv)
            .order_by("-created_time")
        )
        items = [review.item for review in items_queryset[:20]]
        total = items_queryset.count()
    else:
        # Regular shelf type
        shelf_type_enum = ShelfType(shelf_type)
        label = target.shelf_manager.get_label(shelf_type_enum, item_category)
        url = f"{target.url}{shelf_type}/{category}/"
        # Get shelf members for this category and type
        members_queryset = target.shelf_manager.get_latest_members(
            shelf_type_enum, item_category
        ).filter(qv)
        items = [member.item for member in members_queryset[:20]]
        total = members_queryset.count()

    if not label:
        # raise Http404(_("Shelf not found"))
        return HttpResponse()

    return render(
        request,
        "profile_items.html",
        {
            "title": label,
            "url": url,
            "items": items,
            "total": total,
        },
    )
