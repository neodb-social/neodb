from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from catalog.models import *
from common.models.lang import translate
from common.utils import AuthedHttpRequest, get_uuid_or_404

from ..forms import MarkForm
from ..models import Comment, Mark, ShelfManager, ShelfType
from .common import render_list, render_relogin

PAGE_SIZE = 10

_checkmark = "✔️".encode("utf-8")


@login_required
@require_http_methods(["POST"])
def wish(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if not mark.shelf_type:
        mark.update(
            ShelfType.WISHLIST, application_id=getattr(request, "application_id", None)
        )
    if request.GET.get("back"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    return HttpResponse(_checkmark)


@login_required
@require_http_methods(["GET", "POST"])
def mark(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.method == "GET":
        tags = request.user.identity.tag_manager.get_item_tags(item)
        shelf_actions = ShelfManager.get_actions_for_category(item.category)
        shelf_statuses = ShelfManager.get_statuses_for_category(item.category)
        shelf_type = request.GET.get("shelf_type", mark.shelf_type)
        return render(
            request,
            "mark.html",
            {
                "item": item,
                "mark": mark,
                "shelf_type": shelf_type,
                "tags": ",".join(tags),
                "shelf_actions": shelf_actions,
                "shelf_statuses": shelf_statuses,
                "date_today": timezone.localdate().isoformat(),
            },
        )
    else:
        if request.POST.get("delete", default=False):
            mark.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
        else:
            form = MarkForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    mark.update(
                        data["status"],
                        data["text"],
                        data["rating_grade"],
                        data["tags_list"],
                        data["visibility"],
                        share_to_mastodon=data["share_to_mastodon"],
                        created_time=data["mark_date_parsed"],
                        application_id=getattr(request, "application_id", None),
                    )
                except PermissionDenied:
                    logger.warning(f"post to mastodon error 401 {request.user}")
                    return render_relogin(request)
                except ValueError as e:
                    logger.warning(f"post to mastodon error {e} {request.user}")
                    err = (
                        _("Content too long for your Fediverse instance.")
                        if str(e) == "422"
                        else str(e)
                    )
                    return render(
                        request,
                        "common/error.html",
                        {
                            "msg": _(
                                "Data saved but unable to crosspost to Fediverse instance."
                            ),
                            "secondary_msg": err,
                        },
                    )
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
            else:
                # In a real app we'd handle form errors better, but preserving existing behavior of falling through or erroring
                # For now, let's just log and redirect or error if really invalid.
                # The original code didn't strictly validate structure, just tried to cast things.
                logger.warning(f"Mark form invalid: {form.errors}")
                raise BadRequest(_("Invalid input"))


@login_required
@require_http_methods(["POST"])
def mark_log(request: AuthedHttpRequest, item_uuid, log_id):
    """
    Delete log of one item by log id.
    """
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.GET.get("delete", default=False):
        if log_id:
            mark.delete_log(log_id)
        else:
            mark.delete_all_logs()
        return render(request, "_item_user_mark_history.html", {"mark": mark})
    else:
        raise BadRequest(_("Invalid parameter"))


@login_required
@require_http_methods(["GET", "POST"])
def comment(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name not in ["podcastepisode", "tvepisode"]:
        raise BadRequest("Commenting this type of items is not supported yet.")
    comment = Comment.objects.filter(owner=request.user.identity, item=item).first()
    if request.method == "GET":
        return render(
            request,
            "comment.html",
            {
                "item": item,
                "comment": comment,
            },
        )
    else:
        if request.POST.get("delete", default=False):
            if not comment:
                raise Http404(_("Content not found"))
            comment.delete()
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
        visibility = int(request.POST.get("visibility", default=0))
        text = request.POST.get("text")
        position = None
        if item.class_name == "podcastepisode":
            position = request.POST.get("position") or "0:0:0"
            try:
                pos = datetime.strptime(position, "%H:%M:%S")
                position = pos.hour * 3600 + pos.minute * 60 + pos.second
            except Exception:
                if settings.DEBUG:
                    raise
                position = None
        d = {"text": text, "visibility": visibility}
        if position:
            d["metadata"] = {"position": position}
        delete_existing_post = comment is not None and comment.visibility != visibility
        share_to_mastodon = bool(request.POST.get("share_to_mastodon", default=False))
        comment = Comment.objects.update_or_create(
            owner=request.user.identity, item=item, defaults=d
        )[0]
        update_mode = 1 if delete_existing_post else 0
        comment.sync_to_timeline(update_mode)
        if share_to_mastodon:
            comment.sync_to_social_accounts(update_mode)
        comment.update_index()
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@require_http_methods(["POST"])
@login_required
def comment_translate(request, comment_uuid: str):
    comment = Comment.get_by_url(comment_uuid)
    if comment is None:
        raise Http404(_("Content not found"))
    if not comment.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    text = comment.html
    if comment.latest_post:
        lang = comment.latest_post.language
    elif comment.owner.local:
        lang = comment.owner.user.language
    else:
        lang = None
    text = translate(text, request.user.language, lang)
    return HttpResponse(text)


def user_mark_list(request: AuthedHttpRequest, user_name, shelf_type, item_category):
    return render_list(
        request, user_name, "mark", shelf_type=shelf_type, item_category=item_category
    )
