import mimetypes

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.syndication.views import Feed
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.utils import AuthedHttpRequest, PageLinksGenerator, get_uuid_or_404
from journal.models.renderers import convert_leading_space_in_md, render_md
from mastodon.api import boost_toot
from users.models import User
from users.models.apidentity import APIdentity

from ..forms import *
from ..models import *
from .common import render_list


def review_retrieve(request, review_uuid):
    # piece = get_object_or_404(Review, uid=get_uuid_or_404(review_uuid))
    piece = Review.get_by_url(review_uuid)
    if piece is None:
        raise Http404()
    if not piece.is_visible_to(request.user):
        raise PermissionDenied()
    return render(request, "review.html", {"review": piece})


@login_required
def review_edit(request: AuthedHttpRequest, item_uuid, review_uuid=None):
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
                dt = parse_datetime(request.POST.get("mark_date", "") + " 20:00:00")
                mark_date = (
                    dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
                )
            body = form.instance.body
            if request.POST.get("leading_space"):
                body = convert_leading_space_in_md(body)
            review, post = Review.update_item_review(
                item,
                request.user.identity,
                form.cleaned_data["title"],
                body,
                form.cleaned_data["visibility"],
                mark_date,
            )
            if not review:
                raise BadRequest()
            if (
                form.cleaned_data["share_to_mastodon"]
                and request.user.mastodon_username
                and post
            ):
                boost_toot(
                    request.user.mastodon_site,
                    request.user.mastodon_token,
                    post.url,
                )
            return redirect(reverse("journal:review_retrieve", args=[review.uuid]))
        else:
            raise BadRequest()
    else:
        raise BadRequest()


def user_review_list(request, user_name, item_category, year=None):
    return render_list(
        request, user_name, "review", item_category=item_category, year=None
    )


MAX_ITEM_PER_TYPE = 10


class ReviewFeed(Feed):
    def get_object(self, request, id):
        return APIdentity.get_by_handler(id)

    def title(self, owner):
        return "%s的评论" % owner.display_name if owner else "无效链接"

    def link(self, owner):
        return owner.url if owner else settings.SITE_INFO["site_url"]

    def description(self, owner):
        return "%s的评论合集 - NeoDB" % owner.display_name if owner else "无效链接"

    def items(self, user):
        if user is None or user.preference.no_anonymous_view:
            return []
        reviews = Review.objects.filter(owner=user, visibility=0)[:MAX_ITEM_PER_TYPE]
        return reviews

    def item_title(self, item: Review):
        return f"{item.title} - 评论《{item.item.title}》"

    def item_description(self, item: Review):
        target_html = (
            f'<p><a href="{item.item.absolute_url}">{item.item.title}</a></p>\n'
        )
        html = render_md(item.body)
        return target_html + html

    # item_link is only needed if NewsItem has no get_absolute_url method.
    def item_link(self, item: Review):
        return item.absolute_url

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
