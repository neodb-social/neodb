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
from .models import APIdentity, Preference, User
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
def mark_announcements_read(request: AuthedHttpRequest):
    if request.method == "POST":
        try:
            request.user.read_announcement_index = Announcement.objects.latest("pk").pk
            request.user.save(update_fields=["read_announcement_index"])
        except ObjectDoesNotExist:
            # when there is no annoucenment
            pass
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
