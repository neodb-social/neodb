import logging
from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponseRedirect
from django.core.exceptions import BadRequest, PermissionDenied, ObjectDoesNotExist
from django.db.models import Count
from django.utils import timezone
from django.core.paginator import Paginator
from catalog.common.models import ExternalResource, IdealIdTypes
from .models import *
from django.views.decorators.clickjacking import xframe_options_exempt
from journal.models import Mark, ShelfMember, Review, Comment, query_item_category
from journal.models import (
    query_visible,
    query_following,
    update_journal_for_merged_item,
)
from common.utils import PageLinksGenerator, get_uuid_or_404
from common.config import PAGE_LINK_NUMBER
from journal.models import ShelfTypeNames, ShelfType, ItemCategory
from .forms import *
from .search.views import *
from django.http import Http404
from management.models import Announcement
from django.core.cache import cache
import random


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
    skipcheck = request.GET.get("skipcheck", False) and request.user.is_staff
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
    shelf_types = [(n[1], n[2]) for n in iter(ShelfTypeNames) if n[0] == item.category]
    if request.user.is_authenticated:
        visible = query_visible(request.user)
        mark = Mark(request.user, item)
        review = mark.review
        my_collections = item.collections.all().filter(owner=request.user)
        collection_list = (
            item.collections.all()
            .exclude(owner=request.user)
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
            "my_collections": my_collections,
            "collection_list": collection_list,
            "shelf_types": shelf_types,
        },
    )


@login_required
def create(request, item_model):
    if request.method == "GET":
        form_cls = CatalogForms.get(item_model)
        if not form_cls:
            raise BadRequest()
        form = form_cls()
        return render(
            request,
            "catalog_edit.html",
            {
                "form": form,
            },
        )
    elif request.method == "POST":
        form_cls = CatalogForms.get(item_model)
        if not form_cls:
            raise BadRequest()
        form = form_cls(request.POST, request.FILES)
        if form.is_valid():
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(form.errors)
    else:
        raise BadRequest()


@login_required
def edit(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(instance=item)
        if (
            item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        return render(request, "catalog_edit.html", {"form": form, "item": item})
    elif request.method == "POST":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(request.POST, request.FILES, instance=item)
        if (
            item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        if form.is_valid():
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(form.errors)
    else:
        raise BadRequest()


@login_required
def delete(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff and not item.journal_exist:
        raise PermissionDenied()
    for res in item.external_resources.all():
        res.item = None
        res.save()
    item.delete()
    return (
        redirect(item.url + "?skipcheck=1") if request.user.is_staff else redirect("/")
    )


@login_required
def recast(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    cls = request.POST.get("class")
    model = TVShow if cls == "tvshow" else (Movie if cls == "movie" else None)
    if not model:
        raise BadRequest()
    new_item = item.recast_to(model)
    return redirect(new_item.url)


@login_required
def unlink(request):
    if request.method != "POST":
        raise BadRequest()
    if not request.user.is_staff:
        raise PermissionDenied()
    res_id = request.POST.get("id")
    if not res_id:
        raise BadRequest()
    resource = get_object_or_404(ExternalResource, id=res_id)
    resource.item = None
    resource.save()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@login_required
def assign_parent(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    new_item = Item.get_by_url(request.POST.get("parent_item_url"))
    if not new_item or new_item.is_deleted or new_item.merged_to_item_id:
        raise BadRequest("Can't assign parent to a deleted or redirected item")
    if item.__class__ != TVSeason or new_item.__class__ != TVShow:
        raise BadRequest("Can't assign parent for this item")
    if not request.user.is_staff and item.show:
        raise PermissionDenied()
    _logger.warn(f"{request.user} assign {item} to {new_item}")
    item.show = new_item
    item.save(update_fields=["show"])
    return redirect(item.url)


@login_required
def merge(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff and not item.journal_exist:
        raise PermissionDenied()
    if request.POST.get("new_item_url"):
        new_item = Item.get_by_url(request.POST.get("new_item_url"))
        if not new_item or new_item.is_deleted or new_item.merged_to_item_id:
            messages.add_message(request, messages.ERROR, _("不能合并到一个被删除或合并过的条目。"))
            return redirect(item.url)
        if item.class_name == "tvseason" and new_item.class_name == "tvshow":
            pass
        elif item.class_name == "tvshow" and new_item.class_name == "tvseason":
            pass
        elif new_item.class_name != item.class_name:
            messages.add_message(
                request,
                messages.ERROR,
                _("不能合并到一个不同类的条目。") + f" ({item.class_name} to {new_item.class_name})",
            )
            return redirect(item.url)
        _logger.warn(f"{request.user} merges {item} to {new_item}")
        item.merge_to(new_item)
        update_journal_for_merged_item(item_uuid)
        return redirect(new_item.url)
    else:
        if item.merged_to_item:
            _logger.warn(f"{request.user} cancels merge for {item}")
            item.merged_to_item = None
            item.save()
        return redirect(item.url)


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
        queryset = queryset.filter(query_following(request.user))
    else:
        queryset = queryset.filter(query_visible(request.user))
    paginator = Paginator(queryset, NUM_REVIEWS_ON_LIST_PAGE)
    page_number = request.GET.get("page", default=1)
    marks = paginator.get_page(page_number)
    marks.pagination = PageLinksGenerator(
        PAGE_LINK_NUMBER, page_number, paginator.num_pages
    )
    return render(
        request,
        "item_mark_list.html",
        {
            "marks": marks,
            "item": item,
            "followeing_only": following_only,
        },
    )


def review_list(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    queryset = Review.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(query_visible(request.user))
    paginator = Paginator(queryset, NUM_REVIEWS_ON_LIST_PAGE)
    page_number = request.GET.get("page", default=1)
    reviews = paginator.get_page(page_number)
    reviews.pagination = PageLinksGenerator(
        PAGE_LINK_NUMBER, page_number, paginator.num_pages
    )
    return render(
        request,
        "item_review_list.html",
        {
            "reviews": reviews,
            "item": item,
        },
    )


@login_required
def comments(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    queryset = Comment.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(query_visible(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "item_comments.html",
        {
            "comments": queryset[:11],
        },
    )


@login_required
def reviews(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    queryset = Review.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(query_visible(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "item_reviews.html",
        {
            "reviews": queryset[:11],
        },
    )


def discover(request):
    if request.method != "GET":
        raise BadRequest()
    user = request.user
    if user.is_authenticated:
        layout = user.get_preference().discover_layout
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
            for p in user.shelf_manager.get_members(
                ShelfType.PROGRESS, ItemCategory.Podcast
            )
        ]
        recent_podcast_episodes = PodcastEpisode.objects.filter(
            program_id__in=podcast_ids
        ).order_by("-pub_date")[:10]
        books_in_progress = Edition.objects.filter(
            id__in=[
                p.item_id
                for p in user.shelf_manager.get_members(
                    ShelfType.PROGRESS, ItemCategory.Book
                ).order_by("-created_time")[:10]
            ]
        )
        tvshows_in_progress = Item.objects.filter(
            id__in=[
                p.item_id
                for p in user.shelf_manager.get_members(
                    ShelfType.PROGRESS, ItemCategory.TV
                ).order_by("-created_time")[:10]
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
            "gallery_list": gallery_list,
            "recent_podcast_episodes": recent_podcast_episodes,
            "books_in_progress": books_in_progress,
            "tvshows_in_progress": tvshows_in_progress,
            "layout": layout,
        },
    )
