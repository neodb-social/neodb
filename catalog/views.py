from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, F, Window
from django.db.models.functions import RowNumber
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

from common.utils import (
    CustomPaginator,
    PageLinksGenerator,
    get_uuid_or_404,
    user_identity_required,
)
from journal.models import (
    Collection,
    Comment,
    Mark,
    Note,
    Review,
    ShelfManager,
    ShelfMember,
    q_piece_in_home_feed_of_user,
    q_piece_visible_to_user,
)
from takahe.utils import Takahe

from .forms import *
from .models import *
from .search.views import *
from .views_edit import *

NUM_COMMENTS_ON_ITEM_PAGE = 10


def retrieve_by_uuid(request, item_uid):
    item = get_object_or_404(Item, uid=item_uid)
    return redirect(item.url)


def retrieve_redirect(request, item_path, item_uuid):
    return redirect(f"/{item_path}/{item_uuid}", permanent=True)


@require_http_methods(["GET", "HEAD"])
@xframe_options_exempt
def embed(request, item_path, item_uuid):
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404(_("Item not found"))
    if item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if item.is_deleted:
        raise Http404(_("Item no longer exists"))
    focus_item = None
    if request.GET.get("focus"):
        focus_item = get_object_or_404(
            Item, uid=get_uuid_or_404(request.GET.get("focus"))
        )
    if request.method == "HEAD":
        return HttpResponse()
    return render(
        request,
        "embed_" + item.class_name + ".html",
        {"item": item, "focus_item": focus_item},
    )


@require_http_methods(["GET", "HEAD"])
@user_identity_required
def retrieve(request, item_path, item_uuid):
    # item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404(_("Item not found"))
    item_url = f"/{item_path}/{item_uuid}"
    if item.url != item_url:
        return redirect(item.url)
    skipcheck = request.GET.get("skipcheck", False) and request.user.is_authenticated
    if not skipcheck and item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if not skipcheck and item.is_deleted:
        raise Http404(_("Item no longer exists"))
    if request.method == "HEAD":
        return HttpResponse()
    if request.headers.get("Accept", "").endswith("json"):
        return JsonResponse(item.ap_object, content_type="application/activity+json")
    focus_item = None
    if request.GET.get("focus"):
        focus_item = get_object_or_404(
            Item, uid=get_uuid_or_404(request.GET.get("focus"))
        )
    mark = None
    review = None
    my_collections = []
    collection_list = []
    child_item_comments = []
    shelf_actions = ShelfManager.get_actions_for_category(item.category)
    shelf_statuses = ShelfManager.get_statuses_for_category(item.category)
    if request.user.is_authenticated:
        visible = q_piece_visible_to_user(request.user)
        mark = Mark(request.user.identity, item)
        child_item_comments = Comment.objects.filter(
            owner=request.user.identity, item__in=item.child_items.all()
        )
        review = mark.review
        my_collections = item.collections.all().filter(owner=request.user.identity)
        collection_list = (
            item.collections.all()
            .exclude(owner=request.user.identity)
            .filter(visible)
            .annotate(like_counts=Count("likes"))
            .order_by("-like_counts")
        )
    else:
        collection_list = (
            item.collections.all()
            .filter(visibility=0)
            .annotate(like_counts=Count("likes"))
            .order_by("-like_counts")
        )
    return render(
        request,
        item.class_name + ".html",
        {
            "item": item,
            "focus_item": focus_item,
            "mark": mark,
            "review": review,
            "child_item_comments": child_item_comments,
            "my_collections": my_collections,
            "collection_list": collection_list,
            "shelf_actions": shelf_actions,
            "shelf_statuses": shelf_statuses,
        },
    )


def episode_data(request, item_uuid):
    item = get_object_or_404(Podcast, uid=get_uuid_or_404(item_uuid))
    qs = item.episodes.all().order_by("-pub_date")
    if request.GET.get("last"):
        qs = qs.filter(pub_date__lt=request.GET.get("last"))
    return render(
        request, "podcast_episode_data.html", {"item": item, "episodes": qs[:5]}
    )


@login_required
def mark_list(request, item_path, item_uuid, following_only=False):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    queryset = ShelfMember.objects.filter(item=item).order_by("-created_time")
    if following_only:
        queryset = queryset.filter(q_piece_in_home_feed_of_user(request.user))
    else:
        queryset = queryset.filter(q_piece_visible_to_user(request.user))
    paginator = CustomPaginator(queryset, request)
    page_number = request.GET.get("page", default=1)
    marks = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    return render(
        request,
        "item_mark_list.html",
        {
            "marks": marks,
            "item": item,
            "followeing_only": following_only,
            "pagination": pagination,
        },
    )


def review_list(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    queryset = Review.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    paginator = CustomPaginator(queryset, request)
    page_number = request.GET.get("page", default=1)
    reviews = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    return render(
        request,
        "item_review_list.html",
        {
            "reviews": reviews,
            "item": item,
            "pagination": pagination,
        },
    )


def comments(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name == "tvseason":
        ids = [item.pk]
    else:
        ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = Comment.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_comments.html",
        {
            "item": item,
            "comments": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


def comments_by_episode(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    episode_uuid = request.GET.get("episode_uuid")
    if episode_uuid:
        episode = TVEpisode.get_by_url(episode_uuid)
        ids = [episode.pk] if episode else []
    else:
        ids = item.child_item_ids
    queryset = Comment.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_comments_by_episode.html",
        {
            "item": item,
            "episode_uuid": episode_uuid,
            "comments": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


def reviews(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = Review.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_reviews.html",
        {
            "item": item,
            "reviews": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


def notes(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = Note.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    from_note = request.GET.get("from", "")
    if from_note:
        note = get_object_or_404(Note, uid=get_uuid_or_404(from_note))
        queryset = queryset.filter(owner=note.owner)
        queryset = queryset.exclude(pk=note.pk)
    else:
        queryset = queryset.annotate(
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F("owner_id")],
                order_by=F("created_time").desc(),
            ),
            rows=Window(
                expression=Count("owner_id"),
                partition_by=[F("owner_id")],
            ),
        ).filter(row_number=1)
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_notes.html",
        {
            "item": item,
            "from_note": from_note,
            "notes": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


@require_http_methods(["GET", "HEAD"])
def discover(request):
    cache_key = "public_gallery"
    gallery_list = cache.get(cache_key, [])

    # rotate every 6 minutes
    rot = timezone.now().minute // 6
    for gallery in gallery_list:
        items = cache.get(gallery["name"], [])
        i = rot * len(items) // 10
        gallery["items"] = items[i:] + items[:i]

    if request.user.is_authenticated:
        if not request.user.registration_complete:
            return redirect(reverse("users:register"))
        layout = request.user.preference.discover_layout
        identity = request.user.identity
        announcements = []
        if settings.DISCOVER_SHOW_POPULAR_POSTS:
            post_ids = cache.get("popular_posts", [])
            popular_posts = Takahe.get_posts(post_ids).order_by("-published")
        else:
            popular_posts = Takahe.get_public_posts(settings.DISCOVER_SHOW_LOCAL_ONLY)
            # Limit to no more than 3 posts per author
            popular_posts = popular_posts.annotate(
                author_row=Window(
                    expression=RowNumber(),
                    partition_by="author_id",
                    order_by="-published",
                )
            ).filter(author_row__lte=3)
        popular_posts = popular_posts.not_blocked_by(identity.takahe_identity)[:20]  # type: ignore
    else:
        identity = None
        layout = []
        announcements = Takahe.get_announcements()
        popular_posts = []

    collection_ids = cache.get("featured_collections", [])
    if collection_ids:
        i = rot * len(collection_ids) // 10
        collection_ids = collection_ids[i:] + collection_ids[:i]
        featured_collections = Collection.objects.filter(pk__in=collection_ids)
    else:
        featured_collections = []

    if settings.DISCOVER_SHOW_POPULAR_TAGS:
        popular_tags = cache.get("popular_tags", [])
    else:
        popular_tags = None

    updated = cache.get("trends_updated", timezone.now())
    return render(
        request,
        "discover.html",
        {
            "identity": identity,
            "all_announcements": announcements,
            "gallery_list": gallery_list,
            "featured_collections": featured_collections,
            "popular_tags": popular_tags,
            "popular_posts": popular_posts,
            "layout": layout,
            "updated": updated,
        },
    )
