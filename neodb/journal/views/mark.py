import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseBase, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.models.lang import translate
from common.sentry import record_activity
from common.utils import AuthedHttpRequest, get_uuid_or_404

from ..forms import CommentForm, MarkForm
from ..models import Comment, Mark, ShelfManager, ShelfType
from .common import render_list, render_relogin
from common.validators import get_safe_referer_url

logger = logging.getLogger(__name__)

PAGE_SIZE = 10

_checkmark = "✔️".encode("utf-8")


def _redirect_back(request: AuthedHttpRequest) -> HttpResponseRedirect:
    referer = get_safe_referer_url(request)
    return HttpResponseRedirect(referer)


def _mark_saved_response(request: AuthedHttpRequest, item: Item) -> HttpResponseBase:
    """
    After the mark dialog saves or deletes: a plain form submit goes back to
    the referer as before. An htmx submit from an item page reloads it, while
    an inline one (timeline, list cards) closes the dialog and refreshes the
    bookmark icons of that item in place.
    """
    if not request.headers.get("HX-Request"):
        return _redirect_back(request)
    if not request.POST.get("inline"):
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response
    response = HttpResponse(_mark_oob_content(request, item))
    response["HX-Trigger"] = "close_dialog"
    return response


def _mark_oob_content(request: AuthedHttpRequest, item: Item) -> bytes:
    """
    Out-of-band fragments that refresh every card of ``item`` on the page:
    the bookmark action and, on list cards, the viewer's mark details.
    """
    mark = Mark(request.user.identity, item)
    context = {"item": item, "mark": mark, "oob": True}
    return (
        render(request, "action_mark_item.html", context).content
        + render(request, "_list_item_mark.html", context).content
    )


def _mark_error_response(
    request: AuthedHttpRequest,
    msg: str,
    secondary_msg: str = "",
    saved_item: Item | None = None,
) -> HttpResponse:
    """
    Show a message inside the open mark dialog, keeping it open. When the
    mark was saved anyway, also refresh the bookmark icons of that item.
    """
    response = render(
        request,
        "_mark_form_error.html",
        {"msg": msg, "secondary_msg": secondary_msg},
    )
    if saved_item:
        response.content += _mark_oob_content(request, saved_item)
    response["HX-Retarget"] = "#mark-form-error"
    response["HX-Reswap"] = "innerHTML"
    return response


def _form_error_text(form: MarkForm) -> str:
    parts = []
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        text = " ".join(str(e) for e in errors)
        parts.append(f"{label}: {text}" if label else text)
    return "; ".join(parts)


@login_required
@require_http_methods(["POST"])
def wish(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if not mark.shelf_type:
        mark.update(
            ShelfType.WISHLIST, application_id=getattr(request, "application_id", None)
        )
    record_activity("mark", "web")
    if request.GET.get("back"):
        return _redirect_back(request)
    return HttpResponse(_checkmark)


@login_required
@require_http_methods(["POST"])
def follow(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if mark.shelf_type == ShelfType.PROGRESS:
        mark.delete()
    else:
        mark.update(
            ShelfType.PROGRESS, application_id=getattr(request, "application_id", None)
        )
    record_activity("mark", "web")
    return HttpResponseRedirect(item.url)


@login_required
@require_http_methods(["POST"])
def book_progress(request: AuthedHttpRequest, item_uuid: str):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.POST.get("clear"):
        progress_type = None
        progress_value = None
    else:
        progress_type = request.POST.get("progress_type") or None
        progress_value = request.POST.get("progress_value") or None
    try:
        mark.set_progress(progress_type, progress_value)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    record_activity("progress", "web")
    return HttpResponseRedirect(item.url)


@login_required
@require_http_methods(["GET", "POST"])
def mark(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    mark = Mark(request.user.identity, item)
    if request.method == "GET":
        tag_manager = request.user.identity.tag_manager
        tags = tag_manager.get_item_tags(item)
        recent_tags = tag_manager.get_recent_titles()
        popular_tags = tag_manager.get_cached_popular_titles()
        shelf_actions = ShelfManager.get_actions_for_category(item.category)
        shelf_statuses = ShelfManager.get_statuses_for_category(item.category)
        shelf_type = request.GET.get("shelf_type", mark.shelf_type)
        return render(
            request,
            "mark.html",
            {
                "item": item,
                "mark": mark,
                "inline": bool(request.GET.get("inline")),
                "form": MarkForm(
                    initial={
                        "text": mark.comment_text or "",
                        "share_to_mastodon": request.user.preference.mastodon_default_repost,
                    }
                ),
                "shelf_type": shelf_type,
                "tags": tags,
                "recent_tags": recent_tags,
                "popular_tags": popular_tags,
                "shelf_actions": shelf_actions,
                "shelf_statuses": shelf_statuses,
                "date_today": timezone.localdate().isoformat(),
            },
        )
    else:
        if request.POST.get("delete", default=False):
            mark.delete()
            return _mark_saved_response(request, item)
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
                    msg = _("Data saved but unable to crosspost to Fediverse instance.")
                    if request.headers.get("HX-Request"):
                        return _mark_error_response(request, msg, err, item)
                    return render(
                        request,
                        "common/error.html",
                        {"msg": msg, "secondary_msg": err},
                    )
                record_activity("mark", "web")
                return _mark_saved_response(request, item)
            else:
                logger.warning(f"Mark form invalid: {form.errors}")
                if request.headers.get("HX-Request"):
                    return _mark_error_response(
                        request, _("Invalid input"), _form_error_text(form)
                    )
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
                "form": CommentForm(
                    initial={
                        "text": comment.text if comment else "",
                        "visibility": comment.visibility if comment else 0,
                        "share_to_mastodon": request.user.preference.mastodon_default_repost,
                    }
                ),
            },
        )
    else:
        if request.POST.get("delete", default=False):
            if not comment:
                raise Http404(_("Content not found"))
            comment.delete()
            referer = get_safe_referer_url(request)
            return HttpResponseRedirect(referer)
        form = CommentForm(request.POST)
        if not form.is_valid():
            logger.warning(f"Comment form invalid: {form.errors}")
            raise BadRequest(_("Invalid input"))
        visibility = form.cleaned_data["visibility"]
        text = form.cleaned_data["text"]
        position = None
        if item.class_name == "podcastepisode":
            position = form.cleaned_data["position"] or "0:0:0"
            try:
                pos = datetime.strptime(position, "%H:%M:%S")
                position = pos.hour * 3600 + pos.minute * 60 + pos.second
            except Exception:
                if settings.DEBUG:
                    raise
                position = None
        d: dict[str, object] = {"text": text, "visibility": visibility}
        if position:
            d["metadata"] = {"position": position}
        delete_existing_post = comment is not None and comment.visibility != visibility
        share_to_mastodon = form.cleaned_data["share_to_mastodon"]
        comment = Comment.objects.update_or_create(
            owner=request.user.identity, item=item, defaults=d
        )[0]
        update_mode = 1 if delete_existing_post else 0
        comment.sync_to_timeline(update_mode)
        if share_to_mastodon:
            comment.sync_to_social_accounts(update_mode)
        comment.update_index()
        referer = get_safe_referer_url(request)
        return HttpResponseRedirect(referer)


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
