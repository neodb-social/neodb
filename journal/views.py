import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required, permission_required
from django.utils.translation import gettext_lazy as _
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
)
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.paginator import Paginator
from .models import *
from django.conf import settings
from django.http import HttpResponseRedirect
from django.db.models import Q
from management.models import Announcement
from django.utils.baseconv import base62
from .forms import *
from mastodon.api import share_review
from users.views import render_user_blocked, render_user_not_found
from users.models import User, Report, Preference
from common.utils import PageLinksGenerator

_logger = logging.getLogger(__name__)
PAGE_SIZE = 10

_checkmark = "✔️".encode("utf-8")


@login_required
def wish(request, item_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest(b"invalid request")
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    if not item:
        return HttpResponseNotFound(b"item not found")
    request.user.shelf_manager.move_item(item, ShelfType.WISHLIST)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    return HttpResponse(_checkmark)


@login_required
def like(request, piece_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest(b"invalid request")
    piece = get_object_or_404(Collection, uid=base62.decode(piece_uuid))
    if not piece:
        return HttpResponseNotFound(b"piece not found")
    Like.user_like_piece(request.user, piece)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    return HttpResponse(_checkmark)


@login_required
def unlike(request, piece_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest(b"invalid request")
    piece = get_object_or_404(Collection, uid=base62.decode(piece_uuid))
    if not piece:
        return HttpResponseNotFound(b"piece not found")
    Like.user_unlike_piece(request.user, piece)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    return HttpResponse(_checkmark)


@login_required
def add_to_collection(request, item_uuid):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
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
                owner=request.user, title=f"{request.user.username}的收藏单"
            ).id
        collection = Collection.objects.get(owner=request.user, id=cid)
        collection.append_item(item, metadata={"comment": request.POST.get("comment")})
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


def render_relogin(request):
    return render(
        request,
        "common/error.html",
        {
            "url": reverse("users:connect") + "?domain=" + request.user.mastodon_site,
            "msg": _("信息已保存，但是未能分享到联邦网络"),
            "secondary_msg": _(
                "可能是你在联邦网络(Mastodon/Pleroma/...)的登录状态过期了，正在跳转到联邦网络重新登录😼"
            ),
        },
    )


@login_required
def mark(request, item_uuid):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    mark = Mark(request.user, item)
    if request.method == "GET":
        tags = TagManager.get_item_tags_by_user(item, request.user)
        shelf_types = [
            (n[1], n[2]) for n in iter(ShelfTypeNames) if n[0] == item.category
        ]
        shelf_type = request.GET.get("shelf_type", mark.shelf_type)
        return render(
            request,
            "mark.html",
            {
                "item": item,
                "mark": mark,
                "shelf_type": shelf_type,
                "tags": ",".join(tags),
                "shelf_types": shelf_types,
                "date_today": timezone.localdate().isoformat(),
            },
        )
    elif request.method == "POST":
        if request.POST.get("delete", default=False):
            mark.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        else:
            visibility = int(request.POST.get("visibility", default=0))
            rating = request.POST.get("rating", default=0)
            rating = int(rating) if rating else None
            status = ShelfType(request.POST.get("status"))
            text = request.POST.get("text")
            tags = request.POST.get("tags")
            tags = tags.split(",") if tags else []
            share_to_mastodon = bool(
                request.POST.get("share_to_mastodon", default=False)
            )
            mark_date = None
            if request.POST.get("mark_anotherday"):
                mark_date = timezone.get_current_timezone().localize(
                    parse_datetime(request.POST.get("mark_date") + " 20:00:00")
                )
                if mark_date and mark_date >= timezone.now():
                    mark_date = None
            TagManager.tag_item_by_user(item, request.user, tags, visibility)
            try:
                mark.update(
                    status,
                    text,
                    rating,
                    visibility,
                    share_to_mastodon=share_to_mastodon,
                    created_time=mark_date,
                )
            except Exception as e:
                _logger.warn(f"post to mastodon error {e}")
                return render_relogin(request)
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    return HttpResponseBadRequest()


def collection_retrieve(request, collection_uuid):
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied()
    follower_count = collection.likes.all().count()
    following = (
        Like.user_liked_piece(request.user, collection) is not None
        if request.user.is_authenticated
        else False
    )
    return render(
        request,
        "collection.html",
        {
            "collection": collection,
            "follower_count": follower_count,
            "following": following,
        },
    )


def collection_share(request, collection_uuid):
    pass


def collection_retrieve_items(request, collection_uuid, edit=False, msg=None):
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
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
        return HttpResponseBadRequest()
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
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
        return HttpResponseBadRequest()
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    collection.remove_item(item)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
def collection_move_item(request, direction, collection_uuid, item_uuid):
    if request.method != "POST":
        return HttpResponseBadRequest()
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    if direction == "up":
        collection.move_up_item(item)
    else:
        collection.move_down_item(item)
    return collection_retrieve_items(request, collection_uuid, True)


@login_required
def collection_update_item_note(request, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=base62.decode(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied()
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
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
        return HttpResponseBadRequest()


@login_required
def collection_edit(request, collection_uuid=None):
    collection = (
        get_object_or_404(Collection, uid=base62.decode(collection_uuid))
        if collection_uuid
        else None
    )
    if collection and not collection.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        form = CollectionForm(instance=collection) if collection else CollectionForm()
        return render(
            request, "collection_edit.html", {"form": form, "collection": collection}
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
            return HttpResponseBadRequest(form.errors)
    else:
        return HttpResponseBadRequest()


def review_retrieve(request, review_uuid):
    piece = get_object_or_404(Review, uid=base62.decode(review_uuid))
    if not piece.is_visible_to(request.user):
        raise PermissionDenied()
    return render(request, "review.html", {"review": piece})


@login_required
def review_edit(request, item_uuid, review_uuid=None):
    item = get_object_or_404(Item, uid=base62.decode(item_uuid))
    review = (
        get_object_or_404(Review, uid=base62.decode(review_uuid))
        if review_uuid
        else None
    )
    if review and not review.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        form = (
            ReviewForm(instance=review)
            if review
            else ReviewForm(initial={"item": item.id})
        )
        return render(request, "review_edit.html", {"form": form, "item": item})
    elif request.method == "POST":
        form = (
            ReviewForm(request.POST, instance=review)
            if review
            else ReviewForm(request.POST)
        )
        if form.is_valid():
            if not review:
                form.instance.owner = request.user
            form.instance.edited_time = timezone.now()
            form.save()
            if form.cleaned_data["share_to_mastodon"]:
                form.instance.save = lambda **args: None
                form.instance.shared_link = None
                if not share_review(form.instance):
                    return render_relogin(request)
            return redirect(
                reverse("journal:review_retrieve", args=[form.instance.uuid])
            )
        else:
            return HttpResponseBadRequest(form.errors)
    else:
        return HttpResponseBadRequest()


@login_required
def piece_delete(request, piece_uuid):
    piece = get_object_or_404(Piece, uid=base62.decode(piece_uuid))
    return_url = request.GET.get("return_url", None) or "/"
    if not piece.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        return render(
            request, "piece_delete.html", {"piece": piece, "return_url": return_url}
        )
    elif request.method == "POST":
        piece.delete()
        return redirect(return_url)
    else:
        return HttpResponseBadRequest()


def render_list_not_fount(request):
    msg = _("相关列表不存在")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


def _render_list(
    request, user_name, type, shelf_type=None, item_category=None, tag_title=None
):
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if user != request.user and (
        request.user.is_blocked_by(user) or request.user.is_blocking(user)
    ):
        return render_user_blocked(request)
    if type == "mark":
        queryset = user.shelf_manager.get_members(shelf_type, item_category)
    elif type == "tagmember":
        tag = Tag.objects.filter(owner=user, title=tag_title).first()
        if not tag:
            return render_list_not_fount(request)
        if tag.visibility != 0 and user != request.user:
            return render_list_not_fount(request)
        queryset = TagMember.objects.filter(parent=tag)
    elif type == "review":
        queryset = Review.objects.filter(owner=user)
        queryset = queryset.filter(query_item_category(item_category))
    else:
        return HttpResponseBadRequest()
    queryset = queryset.filter(q_visible_to(request.user, user)).order_by(
        "-created_time"
    )
    paginator = Paginator(queryset, PAGE_SIZE)
    page_number = request.GET.get("page", default=1)
    members = paginator.get_page(page_number)
    members.pagination = PageLinksGenerator(PAGE_SIZE, page_number, paginator.num_pages)
    return render(
        request,
        f"user_{type}_list.html",
        {
            "user": user,
            "members": members,
        },
    )


@login_required
def user_mark_list(request, user_name, shelf_type, item_category):
    return _render_list(
        request, user_name, "mark", shelf_type=shelf_type, item_category=item_category
    )


@login_required
def user_tag_member_list(request, user_name, tag_title):
    return _render_list(request, user_name, "tagmember", tag_title=tag_title)


@login_required
def user_review_list(request, user_name, item_category):
    return _render_list(request, user_name, "review", item_category=item_category)


@login_required
def user_tag_list(request, user_name):
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if user != request.user and (
        request.user.is_blocked_by(user) or request.user.is_blocking(user)
    ):
        return render_user_blocked(request)
    tags = Tag.objects.filter(owner=user)
    if user != request.user:
        tags = tags.filter(visibility=0)
    tags = tags.values("title").annotate(total=Count("members")).order_by("-total")
    return render(
        request,
        "user_tag_list.html",
        {
            "user": user,
            "tags": tags,
        },
    )


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
        },
    )


def profile_anonymous(request, id):
    login_url = settings.LOGIN_URL + "?next=" + request.get_full_path()
    try:
        username = id.split("@")[0]
        site = id.split("@")[1]
        return render(
            request,
            "users/home_anonymous.html",
            {
                "login_url": login_url,
                "username": username,
                "site": site,
            },
        )
    except Exception:
        return redirect(login_url)


def profile(request, user_name):
    if request.method != "GET":
        return HttpResponseBadRequest()
    user = User.get(user_name)
    if user is None:
        return render_user_not_found(request)
    if not request.user.is_authenticated and user.get_preference().no_anonymous_view:
        return profile_anonymous(request, user_name)
    # access one's own home page
    if user == request.user:
        reports = Report.objects.order_by("-submitted_time").filter(is_read=False)
        unread_announcements = Announcement.objects.filter(
            pk__gt=request.user.read_announcement_index
        ).order_by("-pk")
        try:
            request.user.read_announcement_index = Announcement.objects.latest("pk").pk
            request.user.save(update_fields=["read_announcement_index"])
        except ObjectDoesNotExist:
            # when there is no annoucenment
            pass
    # visit other's home page
    else:
        if user.is_blocked_by(request.user) or user.is_blocking(request.user):
            return render_user_blocked(request)
        # no these value on other's home page
        reports = None
        unread_announcements = None

    qv = q_visible_to(request.user, user)
    shelf_list = {}
    visbile_categories = [
        ItemCategory.Book,
        ItemCategory.Movie,
        ItemCategory.TV,
        ItemCategory.Music,
        ItemCategory.Game,
    ]
    for category in visbile_categories:
        shelf_list[category] = {}
        for shelf_type in ShelfType:
            members = (
                user.shelf_manager.get_members(shelf_type, category)
                .filter(qv)
                .order_by("-created_time")
            )
            shelf_list[category][shelf_type] = {
                "title": user.shelf_manager.get_label(shelf_type, category),
                "count": members.count(),
                "members": members[:5].prefetch_related("item"),
            }
        reviews = (
            Review.objects.filter(owner=user)
            .filter(qv)
            .filter(query_item_category(category))
            .order_by("-created_time")
        )
        shelf_list[category]["reviewed"] = {
            "title": "评论过的" + category.label,
            "count": reviews.count(),
            "members": reviews[:5].prefetch_related("item"),
        }
    collections = (
        Collection.objects.filter(owner=user).filter(qv).order_by("-created_time")
    )
    liked_collections = (
        Like.user_likes_by_class(user, Collection)
        .order_by("-edited_time")
        .values_list("target_id", flat=True)
    )
    if user != request.user:
        liked_collections = liked_collections.filter(query_visible(request.user))

    return render(
        request,
        "profile.html",
        {
            "user": user,
            "top_tags": user.tag_manager.all_tags[:10],
            "shelf_list": shelf_list,
            "collections": collections[:5],
            "collections_count": collections.count(),
            "liked_collections": [
                Collection.objects.get(id=i)
                for i in liked_collections.order_by("-edited_time")[:5]
            ],
            "liked_collections_count": liked_collections.count(),
            "layout": user.get_preference().profile_layout,
            "reports": reports,
            "unread_announcements": unread_announcements,
        },
    )
