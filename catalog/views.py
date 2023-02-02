import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.utils.translation import gettext_lazy as _
from django.http import (
    HttpResponseBadRequest,
    HttpResponseRedirect,
    HttpResponseNotFound,
)
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Count
from django.utils import timezone
from django.core.paginator import Paginator
from catalog.common.models import ExternalResource
from .models import *
from django.utils.baseconv import base62
from journal.models import Mark, ShelfMember, Review
from journal.models import (
    query_visible,
    query_following,
    update_journal_for_merged_item,
)
from common.utils import PageLinksGenerator
from common.config import PAGE_LINK_NUMBER
from journal.models import ShelfTypeNames
from .forms import *
from .search.views import *
from pprint import pprint

_logger = logging.getLogger(__name__)


NUM_REVIEWS_ON_ITEM_PAGE = 5
NUM_REVIEWS_ON_LIST_PAGE = 20


def retrieve_by_uuid(request, item_uid):
    item = get_object_or_404(Item, uid=item_uid)
    return redirect(item.url)


def retrieve(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=base62.decode(item_uuid))
        item_url = f"/{item_path}/{item_uuid}"
        if item.url != item_url:
            return redirect(item.url)
        skipcheck = request.GET.get("skipcheck", False) and request.user.is_staff
        if not skipcheck and item.merged_to_item:
            return redirect(item.merged_to_item.url)
        if not skipcheck and item.is_deleted:
            return HttpResponseNotFound("item not found")
        focus_item = None
        if request.GET.get("focus"):
            focus_item = get_object_or_404(
                Item, uid=base62.decode(request.GET.get("focus"))
            )
        mark = None
        review = None
        mark_list = None
        review_list = None
        collection_list = []
        shelf_types = [
            (n[1], n[2]) for n in iter(ShelfTypeNames) if n[0] == item.category
        ]
        if request.user.is_authenticated:
            visible = query_visible(request.user)
            mark = Mark(request.user, item)
            review = mark.review
            collection_list = (
                item.collections.all()
                .filter(visible)
                .annotate(like_counts=Count("likes"))
                .order_by("-like_counts")
            )
            mark_query = (
                ShelfMember.objects.filter(item=item)
                .filter(visible)
                .order_by("-created_time")
            )
            mark_list = [
                member.mark for member in mark_query[:NUM_REVIEWS_ON_ITEM_PAGE]
            ]
            review_list = (
                Review.objects.filter(item=item)
                .filter(visible)
                .order_by("-created_time")[:NUM_REVIEWS_ON_ITEM_PAGE]
            )

        return render(
            request,
            item.class_name + ".html",
            {
                "item": item,
                "focus_item": focus_item,
                "mark": mark,
                "review": review,
                "mark_list": mark_list,
                "review_list": review_list,
                "collection_list": collection_list,
                "shelf_types": shelf_types,
            },
        )
    else:
        return HttpResponseBadRequest()


@login_required
def create(request, item_model):
    if request.method == "GET":
        form_cls = CatalogForms[item_model]
        form = form_cls()
        return render(
            request,
            "catalog_edit.html",
            {
                "form": form,
            },
        )
    elif request.method == "POST":
        form_cls = CatalogForms[item_model]
        form = form_cls(request.POST, request.FILES)
        if form.is_valid():
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            return HttpResponseBadRequest(form.errors)
    else:
        return HttpResponseBadRequest()


@login_required
def edit(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=base62.decode(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(instance=item)
        if item.external_resources.all().count() > 0:
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        return render(request, "catalog_edit.html", {"form": form, "item": item})
    elif request.method == "POST":
        item = get_object_or_404(Item, uid=base62.decode(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(request.POST, request.FILES, instance=item)
        if item.external_resources.all().count() > 0:
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        if form.is_valid():
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            return HttpResponseBadRequest(form.errors)
    else:
        return HttpResponseBadRequest()


@login_required
def delete(request, item_path, item_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest()
    if not request.user.is_staff:
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
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
        return HttpResponseBadRequest()
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    cls = request.POST.get("class")
    model = TVShow if cls == "tvshow" else (Movie if cls == "movie" else None)
    if not model:
        return HttpResponseBadRequest("invalid class")
    new_item = item.recast_to(model)
    return redirect(new_item.url)


@login_required
def unlink(request):
    if request.method != "POST":
        return HttpResponseBadRequest()
    if not request.user.is_staff:
        raise PermissionDenied()
    res_id = request.POST.get("id")
    if not res_id:
        return HttpResponseBadRequest()
    resource = get_object_or_404(ExternalResource, id=res_id)
    resource.item = None
    resource.save()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@login_required
def merge(request, item_path, item_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest()
    if not request.user.is_staff:
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    new_item = Item.get_by_url(request.POST.get("new_item_url"))
    if not new_item or new_item.is_deleted or new_item.merged_to_item_id:
        return HttpResponseBadRequest(b"invalid new item")
    _logger.warn(f"{request.user} merges {item} to {new_item}")
    item.merge_to(new_item)
    update_journal_for_merged_item(item_uuid)
    return redirect(new_item.url)


def episode_data(request, item_uuid):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    qs = item.episodes.all().order_by("-pub_date")
    if request.GET.get("last"):
        qs = qs.filter(pub_date__lt=request.GET.get("last"))
    return render(
        request, "podcast_episode_data.html", {"item": item, "episodes": qs[:10]}
    )


@login_required
def mark_list(request, item_path, item_uuid, following_only=False):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    if not item:
        return HttpResponseNotFound(b"item not found")
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
        },
    )


def review_list(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    if not item:
        return HttpResponseNotFound(b"item not found")
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
