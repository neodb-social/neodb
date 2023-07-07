from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext_lazy as _
from management.models import Announcement
from .models import User, Report, Preference
from .forms import ReportForm
from mastodon.api import *
from common.config import *
from .account import *
from .data import *
import json
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import HttpResponseRedirect
from discord import SyncWebhook


def render_user_not_found(request):
    msg = _("😖哎呀，这位用户还没有加入本站，快去联邦宇宙呼唤TA来注册吧！")
    sec_msg = _("")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
            "secondary_msg": sec_msg,
        },
    )


def render_user_blocked(request):
    msg = _("你没有访问TA主页的权限😥")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


@login_required
def followers(request, id):
    if request.method == "GET":
        user = User.get(id)
        if user is None or user != request.user:
            return render_user_not_found(request)
        return render(
            request,
            "users/relation_list.html",
            {
                "user": user,
                "is_followers_page": True,
            },
        )
    else:
        raise BadRequest()


@login_required
def following(request, id):
    if request.method == "GET":
        user = User.get(id)
        if user is None or user != request.user:
            return render_user_not_found(request)
        return render(
            request,
            "users/relation_list.html",
            {
                "user": user,
                "page_type": "followers",
            },
        )
    else:
        raise BadRequest()


@login_required
def follow(request, user_name):
    if request.method != "POST":
        raise BadRequest()
    user = User.get(user_name)
    if request.user.follow(user):
        return render(request, "users/followed.html", context={"user": user})
    else:
        raise BadRequest()


@login_required
def unfollow(request, user_name):
    if request.method != "POST":
        raise BadRequest()
    user = User.get(user_name)
    if request.user.unfollow(user):
        return render(request, "users/unfollowed.html", context={"user": user})
    else:
        raise BadRequest()


@login_required
def follow_locked(request, user_name):
    user = User.get(user_name)
    return render(request, "users/follow_locked.html", context={"user": user})


@login_required
def set_layout(request):
    if request.method == "POST":
        layout = json.loads(request.POST.get("layout"))
        if request.POST.get("name") == "profile":
            request.user.preference.profile_layout = layout
            request.user.preference.save(update_fields=["profile_layout"])
            return redirect(request.user.url)
        elif request.POST.get("name") == "discover":
            request.user.preference.discover_layout = layout
            request.user.preference.save(update_fields=["discover_layout"])
            return redirect(reverse("catalog:discover"))
    raise BadRequest()


@login_required
def report(request):
    if request.method == "GET":
        user_id = request.GET.get("user_id")
        if user_id:
            user = get_object_or_404(User, pk=user_id)
            form = ReportForm(initial={"reported_user": user})
        else:
            form = ReportForm()
        return render(
            request,
            "users/report.html",
            {
                "form": form,
            },
        )
    elif request.method == "POST":
        form = ReportForm(request.POST, request.FILES)
        if form.is_valid():
            form.instance.is_read = False
            form.instance.submit_user = request.user
            form.save()
            dw = settings.DISCORD_WEBHOOKS.get("user-report")
            if dw:
                webhook = SyncWebhook.from_url(dw)
                webhook.send(
                    f"New report from {request.user} about {form.instance.reported_user} : {form.instance.message}"
                )
            return redirect(reverse("common:home"))
        else:
            return render(
                request,
                "users/report.html",
                {
                    "form": form,
                },
            )
    else:
        raise BadRequest()


@login_required
def manage_report(request):
    if not request.user.is_staff:
        raise PermissionDenied()
    if request.method == "GET":
        reports = Report.objects.all()
        for r in reports.filter(is_read=False):
            r.is_read = True
            r.save()
        return render(
            request,
            "users/manage_report.html",
            {
                "reports": reports,
            },
        )
    else:
        raise BadRequest()


@login_required
def mark_announcements_read(request):
    if request.method == "POST":
        try:
            request.user.read_announcement_index = Announcement.objects.latest("pk").pk
            request.user.save(update_fields=["read_announcement_index"])
        except ObjectDoesNotExist:
            # when there is no annoucenment
            pass
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))
