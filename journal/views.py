import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext_lazy as _
from django.http import Http404, HttpResponse
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.paginator import Paginator

from journal.renderers import convert_leading_space_in_md
from .models import *
from django.conf import settings
from django.http import HttpResponseRedirect
from management.models import Announcement
from .forms import *
from mastodon.api import (
    get_spoiler_text,
    share_review,
    share_collection,
    get_status_id_by_url,
    post_toot,
    get_visibility,
)
from users.views import render_user_blocked, render_user_not_found
from users.models import User, Report, Preference
from common.utils import PageLinksGenerator, get_uuid_or_404
from user_messages import api as msg
from datetime import datetime

_logger = logging.getLogger(__name__)
PAGE_SIZE = 10

_checkmark = "✔️".encode("utf-8")


@login_required
def wish(request, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item:
        raise Http404()
    request.user.shelf_manager.move_item(item, ShelfType.WISHLIST)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    return HttpResponse(_checkmark)


@login_required
def like(request, piece_uuid):
    if request.method != "POST":
        raise BadRequest()
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece:
        raise Http404()
    Like.user_like_piece(request.user, piece)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    elif request.GET.get("stats"):
        return render(
            request,
            "like_stats.html",
            {
                "piece": piece,
                "liked": True,
                "label": request.GET.get("label"),
                "icon": request.GET.get("icon"),
            },
        )
    return HttpResponse(_checkmark)


@login_required
def unlike(request, piece_uuid):
    if request.method != "POST":
        raise BadRequest()
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece:
        raise Http404()
    Like.user_unlike_piece(request.user, piece)
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    elif request.GET.get("stats"):
        return render(
            request,
            "like_stats.html",
            {
                "piece": piece,
                "liked": False,
                "label": request.GET.get("label"),
                "icon": request.GET.get("icon"),
            },
        )
    return HttpResponse(_checkmark)


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


def render_relogin(request):
    return render(
        request,
        "common/error.html",
        {
            "url": reverse("users:connect") + "?domain=" + request.user.mastodon_site,
            "msg": _("信息已保存，但是未能分享到联邦宇宙"),
            "secondary_msg": _(
                "可能是你在联邦宇宙(Mastodon/Pleroma/...)的登录状态过期了，正在跳转到联邦宇宙重新登录😼"
            ),
        },
    )


@login_required
def mark(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
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
            silence = request.POST.get("silence", False)
            mark.delete(silence=silence)
            if (
                silence
            ):  # this means the mark is deleted from mark_history, thus redirect to item page
                return redirect(
                    reverse("catalog:retrieve", args=[item.url_path, item.uuid])
                )
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        else:
            visibility = int(request.POST.get("visibility", default=0))
            rating_grade = request.POST.get("rating_grade", default=0)
            rating_grade = int(rating_grade) if rating_grade else None
            status = ShelfType(request.POST.get("status"))
            text = request.POST.get("text")
            tags = request.POST.get("tags")
            tags = tags.split(",") if tags else []
            share_to_mastodon = bool(
                request.POST.get("share_to_mastodon", default=False)
            )
            mark_date = None
            if request.POST.get("mark_anotherday"):
                dt = parse_datetime(request.POST.get("mark_date", "") + " 20:00:00")
                mark_date = (
                    dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
                )
                if mark_date and mark_date >= timezone.now():
                    mark_date = None
            TagManager.tag_item_by_user(item, request.user, tags, visibility)
            try:
                mark.update(
                    status,
                    text,
                    rating_grade,
                    visibility,
                    share_to_mastodon=share_to_mastodon,
                    created_time=mark_date,
                )
            except ValueError as e:
                _logger.warn(f"post to mastodon error {e}")
                return render_relogin(request)
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    raise BadRequest()


def post_comment(user, item, text, visibility, shared_link=None, position=None):
    post_error = False
    status_id = get_status_id_by_url(shared_link)
    link = (
        item.get_absolute_url_with_position(position) if position else item.absolute_url
    )
    action_label = "评论" if text else "分享"
    status = f"{action_label}{ItemCategory(item.category).label}《{item.display_title}》\n{link}\n\n{text}"
    spoiler, status = get_spoiler_text(status, item)
    try:
        response = post_toot(
            user.mastodon_site,
            status,
            get_visibility(visibility, user),
            user.mastodon_token,
            False,
            status_id,
            spoiler,
        )
        if response and response.status_code in [200, 201]:
            j = response.json()
            if "url" in j:
                shared_link = j["url"]
    except Exception as e:
        if settings.DEBUG:
            raise
        post_error = True
    return post_error, shared_link


@login_required
def comment_select_episode(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if request.method == "GET":
        return render(
            request,
            "comment_select_episode.html",
            {
                "item": item,
                "comment": comment,
            },
        )
    raise BadRequest()


@login_required
def mark_log(request, item_uuid, log_id):
    """
    Delete log of one item by log id.
    """
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user, item)
    if request.method == "POST":
        if request.POST.get("delete", default=False):
            mark.delete_log(log_id)
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    raise BadRequest()


@login_required
def mark_history(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user, item)
    if request.method == "GET":
        return render(
            request,
            "mark_history.html",
            {
                "item": item,
                "mark": mark,
            },
        )


@login_required
def comment(request, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not item.class_name in ["podcastepisode", "tvepisode"]:
        raise BadRequest("不支持评论此类型的条目")
    # episode = None
    # if item.class_name == "tvseason":
    #     try:
    #         episode = int(request.POST.get("episode", 0))
    #     except:
    #         episode = 0
    #     if episode <= 0:
    #         raise BadRequest("请输入正确的集数")
    comment = Comment.objects.filter(owner=request.user, item=item).first()
    if request.method == "GET":
        return render(
            request,
            f"comment.html",
            {
                "item": item,
                "comment": comment,
            },
        )
    elif request.method == "POST":
        if request.POST.get("delete", default=False):
            if not comment:
                raise Http404()
            comment.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        visibility = int(request.POST.get("visibility", default=0))
        text = request.POST.get("text")
        position = None
        if item.class_name == "podcastepisode":
            position = request.POST.get("position") or "0:0:0"
            try:
                pos = datetime.strptime(position, "%H:%M:%S")
                position = pos.hour * 3600 + pos.minute * 60 + pos.second
            except:
                if settings.DEBUG:
                    raise
                position = None
        share_to_mastodon = bool(request.POST.get("share_to_mastodon", default=False))
        shared_link = comment.metadata.get("shared_link") if comment else None
        post_error = False
        if share_to_mastodon:
            post_error, shared_link = post_comment(
                request.user, item, text, visibility, shared_link, position
            )
        Comment.objects.update_or_create(
            owner=request.user,
            item=item,
            # metadata__episode=episode,
            defaults={
                "text": text,
                "visibility": visibility,
                "metadata": {
                    "shared_link": shared_link,
                    "position": position,
                },
            },
        )

        # if comment:
        #     comment.visibility = visibility
        #     comment.text = text
        #     comment.metadata["position"] = position
        #     comment.metadata["episode"] = episode
        #     if shared_link:
        #         comment.metadata["shared_link"] = shared_link
        #     comment.save()
        # else:
        #     comment = Comment.objects.create(
        #         owner=request.user,
        #         item=item,
        #         text=text,
        #         visibility=visibility,
        #         metadata={
        #             "shared_link": shared_link,
        #             "position": position,
        #             "episode": episode,
        #         },
        #     )
        if post_error:
            return render_relogin(request)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
    raise BadRequest()


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
            stats["wishlist"] / stats["total"] * 360 if stats["total"] else 0
        )
        stats["progress_deg"] = (
            stats["progress"] / stats["total"] * 360 if stats["total"] else 0
        )
        stats["complete_deg"] = (
            stats["complete"] / stats["total"] * 360 if stats["total"] else 0
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


def review_retrieve(request, review_uuid):
    # piece = get_object_or_404(Review, uid=get_uuid_or_404(review_uuid))
    piece = Review.get_by_url(review_uuid)
    if piece is None:
        raise Http404()
    if not piece.is_visible_to(request.user):
        raise PermissionDenied()
    return render(request, "review.html", {"review": piece})


@login_required
def review_edit(request, item_uuid, review_uuid=None):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    review = (
        get_object_or_404(Review, uid=get_uuid_or_404(review_uuid))
        if review_uuid
        else None
    )
    if review and not review.is_editable_by(request.user):
        raise PermissionDenied()
    if request.method == "GET":
        form = (
            ReviewForm(instance=review)
            if review
            else ReviewForm(initial={"item": item.id, "share_to_mastodon": True})
        )
        return render(
            request,
            "review_edit.html",
            {
                "form": form,
                "item": item,
                "date_today": timezone.localdate().isoformat(),
            },
        )
    elif request.method == "POST":
        form = (
            ReviewForm(request.POST, instance=review)
            if review
            else ReviewForm(request.POST)
        )
        if form.is_valid():
            mark_date = None
            if request.POST.get("mark_anotherday"):
                dt = parse_datetime(request.POST.get("mark_date") + " 20:00:00")
                mark_date = (
                    dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
                )
            body = form.instance.body
            if request.POST.get("leading_space"):
                body = convert_leading_space_in_md(body)
            review = Review.review_item_by_user(
                item,
                request.user,
                form.cleaned_data["title"],
                body,
                form.cleaned_data["visibility"],
                mark_date,
                form.cleaned_data["share_to_mastodon"],
            )
            return redirect(reverse("journal:review_retrieve", args=[review.uuid]))
        else:
            raise BadRequest()
    else:
        raise BadRequest()


@login_required
def piece_delete(request, piece_uuid):
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
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
        raise BadRequest()


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
    tag = None
    if type == "mark":
        queryset = user.shelf_manager.get_latest_members(shelf_type, item_category)
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
        raise BadRequest()
    queryset = queryset.filter(q_visible_to(request.user, user)).order_by(
        "-created_time"
    )
    paginator = Paginator(queryset, PAGE_SIZE)
    page_number = request.GET.get("page", default=1)
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(PAGE_SIZE, page_number, paginator.num_pages)
    return render(
        request,
        f"user_{type}_list.html",
        {"user": user, "members": members, "tag": tag, "pagination": pagination},
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
def user_tag_edit(request):
    if request.method == "GET":
        tag_title = Tag.cleanup_title(request.GET.get("tag", ""), replace=False)
        if not tag_title:
            raise Http404()
        tag = Tag.objects.filter(owner=request.user, title=tag_title).first()
        if not tag:
            raise Http404()
        return render(request, "tag_edit.html", {"tag": tag})
    elif request.method == "POST":
        tag_title = Tag.cleanup_title(request.POST.get("title", ""), replace=False)
        tag_id = request.POST.get("id")
        tag = (
            Tag.objects.filter(owner=request.user, id=tag_id).first()
            if tag_id
            else None
        )
        if not tag or not tag_title:
            msg.error(request.user, _("无效标签"))
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        if request.POST.get("delete"):
            tag.delete()
            msg.info(request.user, _("标签已删除"))
            return redirect(
                reverse("journal:user_tag_list", args=[request.user.mastodon_acct])
            )
        elif (
            tag_title != tag.title
            and Tag.objects.filter(owner=request.user, title=tag_title).exists()
        ):
            msg.error(request.user, _("标签已存在"))
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
        tag.title = tag_title
        tag.visibility = int(request.POST.get("visibility", 0))
        tag.visibility = 0 if tag.visibility == 0 else 2
        tag.save()
        msg.info(request.user, _("标签已修改"))
        return redirect(
            reverse(
                "journal:user_tag_member_list",
                args=[request.user.mastodon_acct, tag.title],
            )
        )
    raise BadRequest()


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
            "liked": True,
        },
    )


def profile(request, user_name):
    if request.method != "GET":
        raise BadRequest()
    user = User.get(user_name, case_sensitive=True)
    if user is None or not user.is_active:
        return render_user_not_found(request)
    if user.mastodon_acct != user_name and user.username != user_name:
        return redirect(user.url)
    if not request.user.is_authenticated and user.get_preference().no_anonymous_view:
        return render(request, "users/home_anonymous.html", {"user": user})
    if user != request.user and (
        user.is_blocked_by(request.user) or user.is_blocking(request.user)
    ):
        return render_user_blocked(request)

    qv = q_visible_to(request.user, user)
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
    for category in visbile_categories:
        shelf_list[category] = {}
        for shelf_type in ShelfType:
            label = user.shelf_manager.get_label(shelf_type, category)
            if label is not None:
                members = user.shelf_manager.get_latest_members(
                    shelf_type, category
                ).filter(qv)
                shelf_list[category][shelf_type] = {
                    "title": label,
                    "count": members.count(),
                    "members": members[:10].prefetch_related("item"),
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
            "members": reviews[:10].prefetch_related("item"),
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
        top_tags = user.tag_manager.public_tags[:10]
    else:
        top_tags = user.tag_manager.all_tags[:10]
    return render(
        request,
        "profile.html",
        {
            "user": user,
            "top_tags": top_tags,
            "shelf_list": shelf_list,
            "collections": collections[:10],
            "collections_count": collections.count(),
            "liked_collections": [
                Collection.objects.get(id=i)
                for i in liked_collections.order_by("-edited_time")[:10]
            ],
            "liked_collections_count": liked_collections.count(),
            "layout": user.get_preference().profile_layout,
        },
    )


def user_calendar_data(request, user_name):
    if request.method != "GET":
        raise BadRequest()
    user = User.get(user_name)
    if user is None or not request.user.is_authenticated:
        return HttpResponse("")
    max_visiblity = max_visiblity_to(request.user, user)
    calendar_data = user.shelf_manager.get_calendar_data(max_visiblity)
    return render(
        request,
        "calendar_data.html",
        {
            "calendar_data": calendar_data,
        },
    )
