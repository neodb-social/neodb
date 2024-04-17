import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.config import *
from common.utils import (
    AuthedHttpRequest,
    HTTPResponseHXRedirect,
    target_identity_required,
)
from mastodon.api import *
from takahe.utils import Takahe

from .account import *
from .data import *
from .models import APIdentity


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
@require_http_methods(["POST"])
def follow(request: AuthedHttpRequest, user_name):
    request.user.identity.follow(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
@require_http_methods(["POST"])
def unfollow(request: AuthedHttpRequest, user_name):
    request.user.identity.unfollow(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
@require_http_methods(["POST"])
def mute(request: AuthedHttpRequest, user_name):
    request.user.identity.mute(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
@require_http_methods(["POST"])
def unmute(request: AuthedHttpRequest, user_name):
    request.user.identity.unmute(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
@require_http_methods(["POST"])
def block(request: AuthedHttpRequest, user_name):
    request.user.identity.block(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@require_http_methods(["POST"])
def unblock(request: AuthedHttpRequest, user_name):
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
@require_http_methods(["POST"])
def accept_follow_request(request: AuthedHttpRequest, user_name):
    request.user.identity.accept_follow_request(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@target_identity_required
@require_http_methods(["POST"])
def reject_follow_request(request: AuthedHttpRequest, user_name):
    request.user.identity.reject_follow_request(request.target_identity)
    return render(
        request,
        "users/profile_actions.html",
        context={"identity": request.target_identity},
    )


@login_required
@require_http_methods(["POST"])
def set_layout(request: AuthedHttpRequest):
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
@require_http_methods(["POST"])
def mark_announcements_read(request: AuthedHttpRequest):
    Takahe.mark_announcements_seen(request.user)
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


def announcements(request):
    return render(
        request,
        "users/announcements.html",
        {"announcements": Takahe.get_announcements()},
    )
