from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from user_messages import api as msg

from catalog.models import *
from users.models import APIdentity

from ..forms import *
from ..models import *
from .common import render_list, target_identity_required

PAGE_SIZE = 10


@target_identity_required
def user_tag_list(request, user_name):
    target: APIdentity = request.target_identity
    category = request.GET.get("category") or ""
    item_category = ItemCategory(category) if category in ItemCategory.values else None
    tags = target.tag_manager.get_tags(
        public_only=target.user != request.user, category=item_category
    )
    return render(
        request,
        "user_tag_list.html",
        {
            "user": target.user,
            "identity": target,
            "tags": tags,
            "category": item_category,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def user_tag_edit(request):
    if request.method == "GET":
        tag_title = Tag.cleanup_title(request.GET.get("tag", ""), replace=False)
        if not tag_title:
            raise Http404(_("Invalid tag"))
        tag = Tag.objects.filter(owner=request.user.identity, title=tag_title).first()
        if not tag:
            raise Http404(_("Tag not found"))
        return render(request, "tag_edit.html", {"tag": tag})
    else:
        tag_title = Tag.cleanup_title(request.POST.get("title", ""), replace=False)
        tag_id = request.POST.get("id")
        tag = (
            Tag.objects.filter(owner=request.user.identity, id=tag_id).first()
            if tag_id
            else None
        )
        if not tag or not tag_title:
            msg.error(request.user, _("Invalid tag"))
            referer = request.META.get("HTTP_REFERER") or ""
            if not url_has_allowed_host_and_scheme(
                referer,
                allowed_hosts=set(settings.SITE_DOMAINS),
                require_https=settings.SSL_ONLY,
            ):
                referer = "/"
            return HttpResponseRedirect(referer)
        if request.POST.get("delete"):
            tag.delete()
            msg.info(request.user, _("Tag deleted."))
            return redirect(
                reverse("journal:user_tag_list", args=[request.user.username])
            )
        elif (
            tag_title != tag.title
            and Tag.objects.filter(
                owner=request.user.identity, title=tag_title
            ).exists()
        ):
            msg.error(request.user, _("Duplicated tag."))
            referer = request.META.get("HTTP_REFERER") or ""
            if not url_has_allowed_host_and_scheme(
                referer,
                allowed_hosts=set(settings.SITE_DOMAINS),
                require_https=settings.SSL_ONLY,
            ):
                referer = "/"
            return HttpResponseRedirect(referer)
        tag.update(
            tag_title,
            int(request.POST.get("visibility", 0)),
            bool(request.POST.get("pinned", 0)),
        )
        msg.info(request.user, _("Tag updated."))
        return redirect(
            reverse(
                "journal:user_tag_member_list",
                args=[request.user.username, tag.title],
            )
        )


def user_tag_member_list(request, user_name, tag_title):
    category = request.GET.get("category") or ""
    item_category = ItemCategory(category) if category in ItemCategory.values else None
    return render_list(
        request,
        user_name,
        "tagmember",
        item_category=item_category,
        tag_title=tag_title,
    )
