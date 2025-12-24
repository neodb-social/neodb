import mimetypes

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.syndication.views import Feed
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import *
from common.models.lang import translate
from common.utils import AuthedHttpRequest, get_uuid_or_404
from users.middlewares import activate_language_for_user
from users.models.apidentity import APIdentity

from ..forms import *
from ..models import *
from ..models.renderers import (
    convert_leading_space_in_md,
    has_spoiler,
    render_md,
)
from .common import render_list


@require_http_methods(["GET"])
def review_retrieve(request, review_uuid):
    # piece = get_object_or_404(Review, uid=get_uuid_or_404(review_uuid))
    piece = Review.get_by_url(review_uuid)
    if piece is None:
        raise Http404(_("Content not found"))
    if not piece.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    return render(request, "review.html", {"review": piece})


@require_http_methods(["POST"])
@login_required
def review_translate(request, review_uuid: str):
    review = Review.get_by_url(review_uuid)
    if review is None:
        raise Http404(_("Content not found"))
    if not review.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    text = review.html_content
    if review.latest_post:
        lang = review.latest_post.language
    elif review.owner.local:
        lang = review.owner.user.language
    else:
        lang = None
    text = translate(text, request.user.language, lang)
    title = translate(review.title, request.user.language, lang)
    return HttpResponse(
        f'<span hx-swap-oob="true" id="review_{review.uuid}_title">{title}</span><div>{text}</div>'
    )


@login_required
@require_http_methods(["GET", "POST"])
def review_edit(request: AuthedHttpRequest, item_uuid, review_uuid=None):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    review = (
        get_object_or_404(Review, uid=get_uuid_or_404(review_uuid))
        if review_uuid
        else None
    )
    if review and not review.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        form = (
            ReviewForm(instance=review)
            if review
            else ReviewForm(
                initial={
                    "item": item.pk,
                    "share_to_mastodon": request.user.preference.mastodon_default_repost,
                }
            )
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
    else:
        form = (
            ReviewForm(request.POST, instance=review)
            if review
            else ReviewForm(request.POST)
        )
        if form.is_valid():
            mark_date = None
            if request.POST.get("mark_anotherday"):
                dt = parse_datetime(request.POST.get("mark_date", "") + " 20:00:00")
                mark_date = (
                    dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
                )
            body = form.instance.body
            if request.POST.get("leading_space"):
                body = convert_leading_space_in_md(body)
            review = Review.update_item_review(
                item,
                request.user.identity,
                form.cleaned_data["title"],
                body,
                form.cleaned_data["visibility"],
                mark_date,
                form.cleaned_data["share_to_mastodon"],
            )
            if not review:
                raise BadRequest(_("Invalid parameter"))
            return redirect(reverse("journal:review_retrieve", args=[review.uuid]))
        else:
            raise BadRequest(_("Invalid parameter"))


def user_review_list(request, user_name, item_category):
    return render_list(request, user_name, "review", item_category=item_category)


MAX_ITEM_PER_TYPE = 10


class ReviewFeed(Feed):
    def __call__(self, request, *args, **kwargs):
        # backward compatible with legacy url format
        try:
            linked_id = APIdentity.get_by_linked_handle(kwargs["username"])
            return redirect(linked_id.url + "feed/reviews/", permanent=True)
        except ObjectDoesNotExist:
            return super().__call__(request, *args, **kwargs)

    def get_object(self, request, *args, **kwargs):
        o = APIdentity.get_by_handle(kwargs["username"])
        if not o.local:
            raise ObjectDoesNotExist(_("User not local"))
        activate_language_for_user(o.user)
        return o

    def title(self, owner):
        return (
            _("Reviews by {0}").format(owner.display_name)
            if owner
            else _("Link invalid")
        )

    def link(self, owner: APIdentity):
        return owner.url if owner else settings.SITE_INFO["site_url"]

    def description(self, owner: APIdentity):
        if not owner:
            return _("Link invalid")
        elif not owner.anonymous_viewable:
            return _("Login required")
        else:
            return _("Reviews by {0}").format(owner.display_name)

    def items(self, owner: APIdentity):
        if owner is None or not owner.anonymous_viewable:
            return []
        reviews = Review.objects.filter(owner=owner, visibility=0)[:MAX_ITEM_PER_TYPE]
        return reviews

    def item_title(self, item: Review):
        s = _("{review_title} - a review of {item_title}").format(
            review_title=item.title, item_title=item.item.title
        )
        if has_spoiler(item.body):
            s += " (" + _("may contain spoiler or triggering content") + ")"
        return s

    def item_description(self, item: Review):
        target_html = (
            f'<p><a href="{item.item.absolute_url}">{item.item.title}</a></p>\n'
        )
        html = render_md(item.body)
        return target_html + html

    # item_link is only needed if NewsItem has no get_absolute_url method.
    def item_link(self, item: Review):
        return str(item.absolute_url)

    def item_categories(self, item):
        return [item.item.category.label]

    def item_pubdate(self, item):
        return item.created_time

    def item_updateddate(self, item):
        return item.edited_time

    def item_enclosure_url(self, item):
        return item.item.cover.url

    def item_enclosure_mime_type(self, item):
        t, _ = mimetypes.guess_type(item.item.cover.url)
        return t

    def item_enclosure_length(self, item):
        try:
            size = item.item.cover.file.size
        except Exception:
            size = None
        return size

    def item_comments(self, item):
        return item.absolute_url
