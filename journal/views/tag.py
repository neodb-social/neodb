from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from user_messages import api as msg

from catalog.models import *
from users.models import APIdentity

from ..forms import *
from ..models import *
from .common import render_list, target_identity_required

PAGE_SIZE = 10


@login_required
@target_identity_required
def user_tag_list(request, user_name):
    target: APIdentity = request.target_identity
    tags = target.tag_manager.get_tags(public_only=target.user != request.user)
    return render(
        request,
        "user_tag_list.html",
        {
            "user": target.user,
            "identity": target,
            "tags": tags,
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
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
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
            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
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
    return render_list(request, user_name, "tagmember", tag_title=tag_title)
