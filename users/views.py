import json

from discord import SyncWebhook
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.config import *
from common.utils import (
    AuthedHttpRequest,
    HTTPResponseHXRedirect,
    target_identity_required,
)
from management.models import Announcement
from mastodon.api import *
from takahe.utils import Takahe

from .account import *
from .data import *
from .forms import ReportForm
from .models import APIdentity, Preference, Report, User
from .profile import account_info, account_profile


def render_user_not_found(request, user_name=""):
    sec_msg = _("😖哎呀，这位用户好像还没有加入本站，快去联邦宇宙呼唤TA来注册吧！")
    msg = _("未找到用户") + user_name
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
            "secondary_msg": sec_msg,
        },
    )


def render_user_blocked(request):
    msg = _("没有访问该用户主页的权限")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


def render_user_noanonymous(request):
    msg = _("作者已设置仅限登录用户查看")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


def query_identity(request, handle):
    try:
        i = APIdentity.get_by_handle(handle)
        return redirect(i.url)
    except APIdentity.DoesNotExist:
        if len(handle.split("@")) == 3:
            Takahe.fetch_remote_identity(handle)
            return render(
                request, "users/fetch_identity_pending.html", {"handle": handle}
            )
        else:
            return render_user_not_found(request, handle)


def fetch_refresh(request):
    handle = request.GET.get("handle", "")
    try:
        i = APIdentity.get_by_handle(handle)
        return HTTPResponseHXRedirect(i.url)
    except Exception:
        retry = int(request.GET.get("retry", 0)) + 1
        if retry > 10:
            return render(request, "users/fetch_identity_failed.html")
        else:
            return render(
                request,
                "users/fetch_identity_refresh.html",
                {"handle": handle, "retry": retry, "delay": retry * 2},
            )


@login_required
@target_identity_required
def follow(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.follow(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
def unfollow(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.unfollow(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
def mute(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.mute(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
def unmute(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.unmute(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
def block(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.block(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
def unblock(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    try:
        target = APIdentity.get_by_handle(user_name)
    except APIdentity.DoesNotExist:
        return render_user_not_found(request)
    target_user = target.user
    if target_user and not target_user.is_active:
        return render_user_not_found(request)
    request.user.identity.unblock(target)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": target},
    )


@login_required
@target_identity_required
def accept_follow_request(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.accept_follow_request(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
def reject_follow_request(request: AuthedHttpRequest, user_name):
    if request.method != "POST":
        raise BadRequest()
    request.user.identity.reject_follow_request(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
def set_layout(request: AuthedHttpRequest):
    if request.method == "POST":
        layout = json.loads(request.POST.get("layout", "{}"))
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
def report(request: AuthedHttpRequest):
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
def manage_report(request: AuthedHttpRequest):
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
def mark_announcements_read(request: AuthedHttpRequest):
    if request.method == "POST":
        try:
            request.user.read_announcement_index = Announcement.objects.latest("pk").pk
            request.user.save(update_fields=["read_announcement_index"])
        except ObjectDoesNotExist:
            # when there is no annoucenment
            pass
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
