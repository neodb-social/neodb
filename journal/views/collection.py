from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item
from common.models import int_
from common.utils import AuthedHttpRequest, get_uuid_or_404
from users.models import User

from ..forms import *
from ..models import *
from .common import render_relogin, target_identity_required


@login_required
def add_to_collection(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if request.method == "GET":
        collections = Collection.objects.filter(owner=request.user.identity)
        return render(
            request,
            "add_to_collection.html",
            {
                "item": item,
                "collections": collections,
            },
        )
    else:
        cid = int_(request.POST.get("collection_id"))
        if not cid:
            cid = Collection.objects.create(
                owner=request.user.identity,
                title=_("Collection by {0}").format(request.user.display_name),
            ).pk
        collection = Collection.objects.get(owner=request.user.identity, id=cid)
        collection.append_item(item, note=request.POST.get("note"))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


def collection_retrieve_redirect(request: AuthedHttpRequest, collection_uuid):
    return redirect(f"/collection/{collection_uuid}", permanent=True)


def collection_retrieve(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    follower_count = collection.likes.all().count()
    following = (
        Like.user_liked_piece(request.user.identity, collection)
        if request.user.is_authenticated
        else False
    )
    featured_since = (
        collection.featured_since(request.user.identity)
        if request.user.is_authenticated
        else None
    )
    available_as_featured = (
        request.user.is_authenticated
        and (following or request.user.identity == collection.owner)
        and not featured_since
        and collection.members.all().exists()
    )
    stats = {}
    if featured_since:
        stats = collection.get_stats(request.user.identity)
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
            "editable": collection.is_editable_by(request.user),
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_add_featured(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    FeaturedCollection.objects.update_or_create(
        owner=request.user.identity, target=collection
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@require_http_methods(["POST"])
def collection_remove_featured(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    fc = FeaturedCollection.objects.filter(
        owner=request.user.identity, target=collection
    ).first()
    if fc:
        fc.delete()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@require_http_methods(["POST", "GET"])
def collection_share(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(
        Collection, uid=get_uuid_or_404(collection_uuid) if collection_uuid else None
    )
    user = request.user
    if collection and not collection.is_visible_to(user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        return render(request, "collection_share.html", {"collection": collection})
    else:
        comment = request.POST.get("comment", "")
        # boost if possible, otherwise quote
        if (
            not comment
            and user.preference.mastodon_repost_mode == 0
            and collection.latest_post
        ):
            if user.mastodon:
                user.mastodon.boost_later(collection.latest_post.url)
        else:
            visibility = VisibilityType(int_(request.POST.get("visibility")))
            link = (
                collection.latest_post.url
                if collection.latest_post
                else collection.absolute_url
            ) or ""
            if not share_collection(collection, comment, user, visibility, link):
                return render_relogin(request)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


def share_collection(
    collection: Collection,
    comment: str,
    user: User,
    visibility: VisibilityType,
    link: str,
):
    if not user or not user.mastodon:
        return
    tags = (
        "\n"
        + user.preference.mastodon_append_tag.replace("[category]", _("collection"))
        if user.preference.mastodon_append_tag
        else ""
    )
    user_str = (
        _("shared my collection")
        if user == collection.owner.user
        else (
            _("shared {username}'s collection").format(
                username=(
                    " @" + collection.owner.user.mastodon.handle + " "
                    if collection.owner.user.mastodon
                    else " " + collection.owner.username + " "
                )
            )
        )
    )
    content = f"{user_str}:{collection.title}\n{link}\n{comment}{tags}"
    try:
        user.mastodon.post(content, visibility)
        return True
    except Exception:
        return False


def collection_retrieve_items(
    request: AuthedHttpRequest, collection_uuid, edit=False, msg=None
):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    members = collection.ordered_members
    last_pos = int_(request.GET.get("last_pos"))
    if last_pos:
        last_member = int_(request.GET.get("last_member"))
        members = members.filter(position__gte=last_pos).exclude(id=last_member)
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": members[:20],
            "collection_edit": edit or request.GET.get("edit"),
            "msg": msg,
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_append_item(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))

    url = request.POST.get("url", "")
    note = request.POST.get("note", "")
    item = Item.get_by_url(url)
    member = None
    if item:
        member, new = collection.append_item(item, note=note)
        if new:
            collection.save()
            msg = None
        else:
            member = None
            msg = _("The item is already in the collection.")
    else:
        msg = _("Unable to find the item, please use item url from this site.")
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": [member] if member else [],
            "collection_edit": True,
            "msg": msg,
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_remove_item(request: AuthedHttpRequest, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    collection.remove_item(item)
    return HttpResponse("")


@login_required
@require_http_methods(["POST"])
def collection_move_item(
    request: AuthedHttpRequest, direction, collection_uuid, item_uuid
):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if direction == "up":
        collection.move_up_item(item)
    else:
        collection.move_down_item(item)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
@require_http_methods(["POST"])
def collection_update_member_order(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    ids = request.POST.get("member_ids", "").strip()
    if not ids:
        raise BadRequest(_("Invalid parameter"))
    ordered_member_ids = [int_(i) for i in ids.split(",")]
    collection.update_member_order(ordered_member_ids)
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": [],
            "collection_edit": True,
            "msg": _("Saved."),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def collection_update_item_note(request: AuthedHttpRequest, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    member = collection.get_member_for_item(item)
    note = request.POST.get("note", default="")
    cancel = request.GET.get("cancel")
    if request.method == "POST" and member:
        member.note = note  # type: ignore
        member.save()
        return render(
            request,
            "collection_update_item_note_ok.html",
            {"collection": collection, "item": item, "collection_member": member},
        )
    elif cancel:
        return render(
            request,
            "collection_update_item_note_ok.html",
            {"collection": collection, "item": item, "collection_member": member},
        )
    else:
        return render(
            request,
            "collection_update_item_note.html",
            {"collection": collection, "item": item, "collection_member": member},
        )


@login_required
@require_http_methods(["GET", "POST"])
def collection_edit(request: AuthedHttpRequest, collection_uuid=None):
    collection = (
        get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
        if collection_uuid
        else None
    )
    if collection and not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
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
                "user": collection.owner.user if collection else request.user,
                "identity": collection.owner if collection else request.user.identity,
            },
        )
    else:
        form = (
            CollectionForm(request.POST, request.FILES, instance=collection)
            if collection
            else CollectionForm(request.POST)
        )
        if form.is_valid():
            if not collection:
                form.instance.owner = request.user.identity
            form.save()
            return redirect(
                reverse("journal:collection_retrieve", args=[form.instance.uuid])
            )
        else:
            raise BadRequest(_("Invalid parameter"))


@target_identity_required
def user_collection_list(request: AuthedHttpRequest, user_name):
    target = request.target_identity
    if not request.user.is_authenticated and not target.anonymous_viewable:
        raise PermissionDenied(_("Login required"))
    collections = (
        Collection.objects.filter(owner=target)
        .filter(q_owned_piece_visible_to_user(request.user, target))
        .order_by("-edited_time")
    )
    return render(
        request,
        "user_collection_list.html",
        {
            "user": target.user,
            "identity": target,
            "collections": collections,
        },
    )


@target_identity_required
def user_liked_collection_list(request: AuthedHttpRequest, user_name):
    target = request.target_identity
    if not request.user.is_authenticated and not target.anonymous_viewable:
        raise PermissionDenied(_("Login required"))
    collections = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    ).order_by("-edited_time")
    if target.user != request.user:
        collections = collections.filter(q_piece_visible_to_user(request.user))
    return render(
        request,
        "user_collection_list.html",
        {
            "user": target.user,
            "identity": target,
            "collections": collections,
            "liked": True,
        },
    )
