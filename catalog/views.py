import logging

from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt

from common.config import PAGE_LINK_NUMBER
from common.utils import PageLinksGenerator, get_uuid_or_404
from journal.models import (
    Comment,
    Mark,
    Review,
    ShelfMember,
    ShelfType,
    ShelfTypeNames,
    q_item_in_category,
    q_piece_in_home_feed_of_user,
    q_piece_visible_to_user,
)

from .forms import *
from .models import *
from .search.views import *
from .views_edit import *

_logger = logging.getLogger(__name__)


NUM_REVIEWS_ON_ITEM_PAGE = 5
NUM_REVIEWS_ON_LIST_PAGE = 20


def retrieve_by_uuid(request, item_uid):
    item = get_object_or_404(Item, uid=item_uid)
    return redirect(item.url)


@xframe_options_exempt
def embed(request, item_path, item_uuid):
    if request.method != "GET":
        raise BadRequest()
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404()
    if item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if item.is_deleted:
        raise Http404()
    focus_item = None
    if request.GET.get("focus"):
        focus_item = get_object_or_404(
            Item, uid=get_uuid_or_404(request.GET.get("focus"))
        )
    return render(
        request,
        "embed_" + item.class_name + ".html",
        {"item": item, "focus_item": focus_item},
    )


def retrieve(request, item_path, item_uuid):
    if request.method != "GET":
        raise BadRequest()
    # item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404()
    item_url = f"/{item_path}/{item_uuid}"
    if item.url != item_url:
        return redirect(item.url)
    if request.headers.get("Accept", "").endswith("json"):
        return redirect(item.api_url)
    skipcheck = request.GET.get("skipcheck", False) and request.user.is_authenticated
    if not skipcheck and item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if not skipcheck and item.is_deleted:
        raise Http404()
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
    shelf_types = [(n[1], n[2]) for n in iter(ShelfTypeNames) if n[0] == item.category]
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
            "shelf_types": shelf_types,
        },
    )


def episode_data(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    qs = item.episodes.all().order_by("-pub_date")
    if request.GET.get("last"):
        qs = qs.filter(pub_date__lt=request.GET.get("last"))
    return render(
        request, "podcast_episode_data.html", {"item": item, "episodes": qs[:5]}
    )


@login_required
def mark_list(request, item_path, item_uuid, following_only=False):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    queryset = ShelfMember.objects.filter(item=item).order_by("-created_time")
    if following_only:
        queryset = queryset.filter(q_piece_in_home_feed_of_user(request.user))
    else:
        queryset = queryset.filter(q_piece_visible_to_user(request.user))
    paginator = Paginator(queryset, NUM_REVIEWS_ON_LIST_PAGE)
    page_number = request.GET.get("page", default=1)
    marks = paginator.get_page(page_number)
    pagination = PageLinksGenerator(PAGE_LINK_NUMBER, page_number, paginator.num_pages)
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
    if not item:
        raise Http404()
    queryset = Review.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    paginator = Paginator(queryset, NUM_REVIEWS_ON_LIST_PAGE)
    page_number = request.GET.get("page", default=1)
    reviews = paginator.get_page(page_number)
    pagination = PageLinksGenerator(PAGE_LINK_NUMBER, page_number, paginator.num_pages)
    return render(
        request,
        "item_review_list.html",
        {
            "reviews": reviews,
            "item": item,
            "pagination": pagination,
        },
    )


@login_required
def comments(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    ids = item.child_item_ids + [item.id]
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
            "comments": queryset[:11],
        },
    )


@login_required
def comments_by_episode(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
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
            "comments": queryset[:11],
        },
    )


@login_required
def reviews(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    ids = item.child_item_ids + [item.id]
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
            "reviews": queryset[:11],
        },
    )


def discover(request):
    if request.method != "GET":
        raise BadRequest()
    user = request.user
    if user.is_authenticated:
        layout = user.preference.discover_layout
    else:
        layout = []

    cache_key = "public_gallery"
    gallery_list = cache.get(cache_key, [])

    # for gallery in gallery_list:
    #     ids = (
    #         random.sample(gallery["item_ids"], 10)
    #         if len(gallery["item_ids"]) > 10
    #         else gallery["item_ids"]
    #     )
    #     gallery["items"] = Item.objects.filter(id__in=ids)

    if user.is_authenticated:
        podcast_ids = [
            p.item_id
            for p in user.shelf_manager.get_latest_members(
                ShelfType.PROGRESS, ItemCategory.Podcast
            )
        ]
        recent_podcast_episodes = PodcastEpisode.objects.filter(
            program_id__in=podcast_ids
        ).order_by("-pub_date")[:10]
        books_in_progress = Edition.objects.filter(
            id__in=[
                p.item_id
                for p in user.shelf_manager.get_latest_members(
                    ShelfType.PROGRESS, ItemCategory.Book
                )[:10]
            ]
        )
        tvshows_in_progress = Item.objects.filter(
            id__in=[
                p.item_id
                for p in user.shelf_manager.get_latest_members(
                    ShelfType.PROGRESS, ItemCategory.TV
                )[:10]
            ]
        )
    else:
        recent_podcast_episodes = []
        books_in_progress = []
        tvshows_in_progress = []

    return render(
        request,
        "discover.html",
        {
            "user": user,
            "identity": user.identity,
            "gallery_list": gallery_list,
            "recent_podcast_episodes": recent_podcast_episodes,
            "books_in_progress": books_in_progress,
            "tvshows_in_progress": tvshows_in_progress,
            "layout": layout,
        },
    )
