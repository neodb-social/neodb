from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import PageLinksGenerator, get_uuid_or_404
from journal.models.renderers import convert_leading_space_in_md
from mastodon.api import share_collection
from users.models import User
from users.views import render_user_blocked, render_user_not_found

from ..forms import *
from ..models import *
from .common import render_relogin


@login_required
def add_to_collection(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if request.method == "GET":
        collections = Collection.objects.filter(owner=request.user)
        return render(
            request,
            "add_to_collection.html",
            {
                "item": item,
                "collections": collections,
            },
        )
    else:
        cid = int(request.POST.get("collection_id", default=0))
        if not cid:
            cid = Collection.objects.create(
                owner=request.user, title=f"{request.user.display_name}的收藏单"
            ).id
        collection = Collection.objects.get(owner=request.user, id=cid)
        collection.append_item(item, note=request.POST.get("note"))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


def collection_retrieve(request, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied()
    follower_count = collection.likes.all().count()
    following = (
        Like.user_liked_piece(request.user, collection)
        if request.user.is_authenticated
        else False
    )
    featured_since = (
        collection.featured_by_user_since(request.user)
        if request.user.is_authenticated
        else None
    )
    available_as_featured = (
        request.user.is_authenticated
        and (following or request.user == collection.owner)
        and not featured_since
        and collection.members.all().exists()
    )
    stats = {}
    if featured_since:
        stats = collection.get_stats_for_user(request.user)
        stats["wishlist_deg"] = (
            round(stats["wishlist"] / stats["total"] * 360) if stats["total"] else 0
        )
        stats["progress_deg"] = (
            round(stats["progress"] / stats["total"] * 360) if stats["total"] else 0
        )
        stats["complete_deg"] = (
            round(stats["complete"] / stats["total"] * 360) if stats["total"] else 0
        )
    return render(
        request,
        "collection.html",
        {
            "collection": collection,
            "follower_count": follower_count,
            "following": following,
            "stats": stats,
            "available_as_featured": available_as_featured,
            "featured_since": featured_since,
        },
    )


@login_required
def collection_add_featured(request, collection_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied()
    FeaturedCollection.objects.update_or_create(owner=request.user, target=collection)
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@login_required
def collection_remove_featured(request, collection_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied()
    fc = FeaturedCollection.objects.filter(
        owner=request.user, target=collection
    ).first()
    if fc:
        fc.delete()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@login_required
def collection_share(request, collection_uuid):
    collection = (
        get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
        if collection_uuid
        else None
    )
    if collection and not collection.is_visible_to(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        return render(request, "collection_share.html", {"collection": collection})
    elif request.method == "POST":
        visibility = int(request.POST.get("visibility", default=0))
        comment = request.POST.get("comment")
        if share_collection(collection, comment, request.user, visibility):
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        else:
            return render_relogin(request)
    else:
        raise BadRequest()


def collection_retrieve_items(request, collection_uuid, edit=False, msg=None):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied()
    form = CollectionForm(instance=collection)
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "form": form,
            "collection_edit": edit or request.GET.get("edit"),
            "msg": msg,
        },
    )


@login_required
def collection_append_item(request, collection_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()

    url = request.POST.get("url")
    note = request.POST.get("note")
    item = Item.get_by_url(url)
    if item:
        collection.append_item(item, note=note)
        collection.save()
        msg = None
    else:
        msg = _("条目链接无法识别，请输入本站已有条目的链接。")
    return collection_retrieve_items(request, collection_uuid, True, msg)


@login_required
def collection_remove_item(request, collection_uuid, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    collection.remove_item(item)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
def collection_move_item(request, direction, collection_uuid, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if direction == "up":
        collection.move_up_item(item)
    else:
        collection.move_down_item(item)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
def collection_update_member_order(request, collection_uuid):
    if request.method != "POST":
        raise BadRequest()
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    ids = request.POST.get("member_ids", "").strip()
    if not ids:
        raise BadRequest()
    ordered_member_ids = [int(i) for i in ids.split(",")]
    collection.update_member_order(ordered_member_ids)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
def collection_update_item_note(request, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "POST":
        collection.update_item_metadata(
            item, {"note": request.POST.get("note", default="")}
        )
        return collection_retrieve_items(request, collection_uuid, True)
    elif request.method == "GET":
        member = collection.get_member_for_item(item)
        return render(
            request,
            "collection_update_item_note.html",
            {"collection": collection, "item": item, "note": member.note},
        )
    else:
        raise BadRequest()


@login_required
def collection_edit(request, collection_uuid=None):
    collection = (
        get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
        if collection_uuid
        else None
    )
    if collection and not collection.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        form = CollectionForm(instance=collection) if collection else CollectionForm()
        if request.GET.get("title"):
            form.instance.title = request.GET.get("title")
        return render(
            request,
            "collection_edit.html",
            {
                "form": form,
                "collection": collection,
                "user": collection.owner if collection else request.user,
            },
        )
    elif request.method == "POST":
        form = (
            CollectionForm(request.POST, request.FILES, instance=collection)
            if collection
            else CollectionForm(request.POST)
        )
        if form.is_valid():
            if not collection:
                form.instance.owner = request.user
            form.instance.edited_time = timezone.now()
            form.save()
            return redirect(
                reverse("journal:collection_retrieve", args=[form.instance.uuid])
            )
        else:
            raise BadRequest()
    else:
        raise BadRequest()


@login_required
def user_collection_list(request, user_name):
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if user != request.user and (
        request.user.is_blocked_by(user) or request.user.is_blocking(user)
    ):
        return render_user_blocked(request)
    collections = Collection.objects.filter(owner=user)
    if user != request.user:
        if request.user.is_following(user):
            collections = collections.filter(visibility__in=[0, 1])
        else:
            collections = collections.filter(visibility=0)
    return render(
        request,
        "user_collection_list.html",
        {
            "user": user,
            "collections": collections,
        },
    )


@login_required
def user_liked_collection_list(request, user_name):
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if user != request.user and (
        request.user.is_blocked_by(user) or request.user.is_blocking(user)
    ):
        return render_user_blocked(request)
    collections = Collection.objects.filter(likes__owner=user)
    if user != request.user:
        collections = collections.filter(query_visible(request.user))
    return render(
        request,
        "user_collection_list.html",
        {
            "user": user,
            "collections": collections,
            "liked": True,
        },
    )
