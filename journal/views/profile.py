import datetime
from urllib.parse import quote_plus

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, prefetch_related_objects
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.models.misc import int_
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
from ..models.common import prefetch_pieces_for_posts
from ..search import JournalIndex


@require_http_methods(["GET", "HEAD"])
@profile_identity_required
def profile(request: AuthedHttpRequest, user_name):
    target = request.target_identity
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

    anonymous = not request.user.is_authenticated
    if anonymous:
        # anonymous user should not see remote user's content
        if not target.local:
            return redirect(target.profile_uri or target.actor_uri)
        # anonymous user should not see local user's content unless permitted
        # anonymous user should not see group (for now)
        elif not target.anonymous_viewable or target.is_group:
            return render(
                request,
                "users/home_anonymous.html",
                {
                    "identity": target,
                    "redir": f"/account/login?next={quote_plus(target.url)}",
                },
            )

    feed_view = target.is_group or (
        not target.local
        and target.domain_name not in Takahe.get_neodb_peers(active_only=False)
    )
    if feed_view:
        return render(
            request,
            "profile.html",
            {
                "user": target.user,
                "identity": target,
                "me": False,
                "top_tags": None,
                "recent_posts": None,
                "feed_view": True,
                "shelf_list": {},
                "collections_count": 0,
                "pinned_collections": [],
                "liked_collections_count": 0,
                "layout": [],
                "year": None,
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
        if today.month >= 12:
            year = today.year
        elif today.month <= 1:
            year = today.year - 1
        else:
            year = None
    liked_collections_count = liked_collections_queryset.count()
    top_tags = target.tag_manager.get_tags(public_only=not me, pinned_only=True)[:10]
    if not top_tags.exists():
        top_tags = target.tag_manager.get_tags(public_only=not me)[:10]
    viewer_identity_pk = None if anonymous else request.user.identity.pk
    if target.is_group:
        recent_posts = list(Takahe.get_boosted_posts(target.pk)[:10])
    else:
        recent_posts = list(Takahe.get_recent_posts(target.pk, viewer_identity_pk)[:10])
    prefetch_pieces_for_posts(recent_posts)
    default_layout.append({"id": "collection_created", "visibility": True})
    default_layout.append({"id": "collection_marked", "visibility": True})
    default_layout.append({"id": "people_person_following", "visibility": True})
    default_layout.append({"id": "people_organization_following", "visibility": True})
    pinned_collections = list(
        Collection.objects.filter(
            interactions__interaction_type="pin", interactions__identity=target
        )
        .order_by("-interactions__created_time")
        .filter(qv)
        .select_related("owner", "owner__user")[:10]
    )
    # _sidebar.html iterates identity.featured_collections, calls is_visible_to
    # (hits owner, owner.user, owner.takahe_identity) and get_stats
    # (hits collection_members + shelf counts) for each one. Prefetch eagerly
    # so those derefs reuse cached rows.
    prefetch_related_objects(
        [target],
        Prefetch(
            "featured_collections",
            queryset=Collection.objects.select_related("owner", "owner__user"),
        ),
    )
    featured_owners = [
        c.owner
        for c in target.featured_collections.all()  # ty: ignore[unresolved-attribute]
        if getattr(c, "owner_id", None)
    ]
    Takahe.prefetch_takahe_identities(
        [c.owner for c in pinned_collections if c.owner_id] + featured_owners
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
@profile_identity_required
def profile_posts_data(request: AuthedHttpRequest, user_name):
    target = request.target_identity
    last_pk = int_(request.GET.get("last", 0))
    viewer_pk = request.user.identity.pk
    if target.is_group:
        qs = Takahe.get_boosted_posts(target.pk, viewer_pk=viewer_pk, days=None)
        if last_pk:
            qs = qs.filter(boost_pk__lt=last_pk)
        posts = list(qs[:20])
    else:
        qs = Takahe.get_recent_posts(target.pk, viewer_pk, days=None)
        if last_pk:
            qs = qs.filter(pk__lt=last_pk)
        posts = list(qs.order_by("-pk")[:20])
    prefetch_pieces_for_posts(posts)
    return render(
        request,
        "profile_posts.html",
        {"posts": posts, "user_name": user_name, "is_group": target.is_group},
    )


@require_http_methods(["GET", "HEAD"])
@target_identity_required
def user_calendar_data(request, user_name):
    if request.method == "HEAD":
        return HttpResponse()
    target = request.target_identity
    if not request.user.is_authenticated and target.is_group:
        return HttpResponse()
    max_visiblity = max_visiblity_to_user(request.user, target)
    if max_visiblity == 2:
        calendar_data = target.shelf_manager.get_calendar_data(max_visiblity)
    else:
        cache_key = f"user_calendar:{target.pk}:{max_visiblity}"
        calendar_data = cache.get(cache_key)
        if calendar_data is None:
            calendar_data = target.shelf_manager.get_calendar_data(max_visiblity)
            cache.set(cache_key, calendar_data, timeout=3600)
    return render(
        request,
        "calendar_data.html",
        {
            "calendar_data": calendar_data,
        },
    )


@require_http_methods(["GET", "HEAD"])
def profile_collection_items(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        # raise PermissionDenied(_("Insufficient permission"))
        return HttpResponse()
    if request.method == "HEAD":
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


_FOLLOW_LIST_PAGE_SIZE = 20


@require_http_methods(["GET", "HEAD"])
@login_required
@target_identity_required
def user_follow_list(request: AuthedHttpRequest, user_name, list_type: str):
    target = request.target_identity
    viewer = request.identity
    if target.user != request.user:
        if not (viewer and viewer.is_following(target) and target.is_following(viewer)):
            raise PermissionDenied(_("Access denied"))
    if request.method == "HEAD":
        return HttpResponse()
    last_pk = int_(request.GET.get("last", 0))
    match list_type:
        case "following":
            ids = Takahe.get_following_page(target.pk, last_pk, _FOLLOW_LIST_PAGE_SIZE)
            identities = list(APIdentity.objects.filter(pk__in=ids).order_by("pk"))
            title = _("Following")
        case "followers":
            ids = Takahe.get_follower_page(target.pk, last_pk, _FOLLOW_LIST_PAGE_SIZE)
            identities = list(APIdentity.objects.filter(pk__in=ids).order_by("pk"))
            title = _("Followers")
        case "mutuals":
            ids = Takahe.get_mutual_page(target.pk, last_pk, _FOLLOW_LIST_PAGE_SIZE)
            identities = list(APIdentity.objects.filter(pk__in=ids).order_by("pk"))
            title = _("Mutuals")
        case _:
            raise Http404()
    context = {
        "user": target.user,
        "identity": target,
        "identities": identities,
        "list_type": list_type,
        "title": title,
        "user_name": user_name,
        "next_cursor": identities[-1].pk if identities else None,
    }
    if request.headers.get("HX-Request"):
        return render(request, "user_follow_list_items.html", context)
    return render(request, "user_follow_list.html", context)


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

    # Optional sub-filter: people_type splits the People category into
    # person / organization rows on the profile page.
    people_type = request.GET.get("people_type") or ""
    if people_type and (
        item_category != ItemCategory.People or people_type not in PeopleType.values
    ):
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
            .prefetch_related("item", "item__external_resources")
        )
        items = [review.item for review in items_queryset[:20]]
        total = items_queryset.count()
    else:
        # Regular shelf type
        shelf_type_enum = ShelfType(shelf_type)
        label = target.shelf_manager.get_label(shelf_type_enum, item_category)
        url = f"{target.url}{shelf_type}/{category}/"
        # Get shelf members for this category and type
        members_queryset = (
            target.shelf_manager.get_latest_members(shelf_type_enum, item_category)
            .filter(qv)
            .prefetch_related("item", "item__external_resources")
        )
        if people_type:
            members_queryset = members_queryset.filter(
                item__people__people_type=people_type
            )
            if people_type == PeopleType.PERSON:
                label = _("People")
            else:
                label = _("Organizations")
            url = f"{url}?people_type={people_type}"
        items = [member.item for member in members_queryset[:20]]
        total = members_queryset.count()
    if items:
        Item.prefetch_parent_items(items)
        Rating.attach_to_items(items)

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
