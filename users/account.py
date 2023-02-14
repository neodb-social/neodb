from django.shortcuts import reverse, redirect, render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import auth
from django.contrib.auth import authenticate
from django.core.paginator import Paginator
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist, BadRequest
from django.db.models import Count
from .models import User, Report, Preference
from .forms import ReportForm
from mastodon.api import *
from mastodon import mastodon_request_included
from common.config import *
from mastodon.models import MastodonApplication
from mastodon.api import verify_account
from django.conf import settings
from urllib.parse import quote
import django_rq
from .account import *
from .tasks import *
from datetime import timedelta
from django.utils import timezone
from django.contrib import messages
from journal.models import remove_data_by_user


# the 'login' page that user can see
def login(request):
    if request.method == "GET":
        selected_site = request.GET.get("site", default="")

        sites = MastodonApplication.objects.all().order_by("domain_name")

        # store redirect url in the cookie
        if request.GET.get("next"):
            request.session["next_url"] = request.GET.get("next")

        return render(
            request,
            "users/login.html",
            {
                "sites": sites,
                "scope": quote(settings.MASTODON_CLIENT_SCOPE),
                "selected_site": selected_site,
                "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
            },
        )
    else:
        raise BadRequest()


# connect will redirect to mastodon server
def connect(request):
    login_domain = (
        request.session["swap_domain"]
        if request.session.get("swap_login")
        else request.GET.get("domain")
    )
    if not login_domain:
        return render(
            request,
            "common/error.html",
            {
                "msg": "未指定实例域名",
                "secondary_msg": "",
            },
        )
    login_domain = (
        login_domain.strip().lower().split("//")[-1].split("/")[0].split("@")[-1]
    )
    app, error_msg = get_mastodon_application(login_domain)
    if app is None:
        return render(
            request,
            "common/error.html",
            {
                "msg": error_msg,
                "secondary_msg": "",
            },
        )
    else:
        login_url = get_mastodon_login_url(app, login_domain, request)
        resp = redirect(login_url)
        resp.set_cookie("mastodon_domain", app.domain_name)
        return resp


# mastodon server redirect back to here
@mastodon_request_included
def OAuth2_login(request):
    if request.method != "GET":
        raise BadRequest()

    code = request.GET.get("code")
    if not code:
        return render(
            request,
            "common/error.html",
            {"msg": _("认证失败😫"), "secondary_msg": _("Mastodon服务未能返回有效认证信息")},
        )
    site = request.COOKIES.get("mastodon_domain")
    if not code:
        return render(
            request,
            "common/error.html",
            {"msg": _("认证失败😫"), "secondary_msg": _("无效Cookie信息")},
        )
    try:
        token, refresh_token = obtain_token(site, request, code)
    except ObjectDoesNotExist:
        raise BadRequest()
    if not token:
        return render(
            request,
            "common/error.html",
            {"msg": _("认证失败😫"), "secondary_msg": _("Mastodon服务未能返回有效认证令牌")},
        )

    if (
        request.session.get("swap_login", False) and request.user.is_authenticated
    ):  # swap login for existing user
        return swap_login(request, token, site, refresh_token)

    user = authenticate(request, token=token, site=site)
    if user:  # existing user
        user.mastodon_token = token
        user.mastodon_refresh_token = refresh_token
        user.save(update_fields=["mastodon_token", "mastodon_refresh_token"])
        auth_login(request, user)
        if request.session.get("next_url") is not None:
            response = redirect(request.session.get("next_url"))
            del request.session["next_url"]
        else:
            response = redirect(reverse("common:home"))
        return response
    else:  # newly registered user
        code, user_data = verify_account(site, token)
        if code != 200 or user_data is None:
            return render(request, "common/error.html", {"msg": _("联邦网络访问失败😫")})
        new_user = User(
            username=user_data["username"],
            mastodon_id=user_data["id"],
            mastodon_site=site,
            mastodon_token=token,
            mastodon_refresh_token=refresh_token,
            mastodon_account=user_data,
        )
        new_user.save()
        Preference.objects.create(user=new_user)
        request.session["new_user"] = True
        auth_login(request, new_user)
        return redirect(reverse("users:register"))


@mastodon_request_included
@login_required
def logout(request):
    if request.method == "GET":
        # revoke_token(request.user.mastodon_site, request.user.mastodon_token)
        auth_logout(request)
        return redirect(reverse("users:login"))
    else:
        raise BadRequest()


@mastodon_request_included
@login_required
def reconnect(request):
    if request.method == "POST":
        request.session["swap_login"] = True
        request.session["swap_domain"] = request.POST["domain"]
        return connect(request)
    else:
        raise BadRequest()


@mastodon_request_included
def register(request):
    if request.session.get("new_user"):
        del request.session["new_user"]
        return render(request, "users/register.html")
    else:
        return redirect(reverse("common:home"))


def swap_login(request, token, site, refresh_token):
    del request.session["swap_login"]
    del request.session["swap_domain"]
    code, data = verify_account(site, token)
    current_user = request.user
    if code == 200 and data is not None:
        username = data["username"]
        if username == current_user.username and site == current_user.mastodon_site:
            messages.add_message(
                request, messages.ERROR, _(f"该身份 {username}@{site} 与当前账号相同。")
            )
        else:
            try:
                existing_user = User.objects.get(username=username, mastodon_site=site)
                messages.add_message(
                    request, messages.ERROR, _(f"该身份 {username}@{site} 已被用于其它账号。")
                )
            except ObjectDoesNotExist:
                current_user.username = username
                current_user.mastodon_id = data["id"]
                current_user.mastodon_site = site
                current_user.mastodon_token = token
                current_user.mastodon_refresh_token = refresh_token
                current_user.mastodon_account = data
                current_user.save(
                    update_fields=[
                        "username",
                        "mastodon_id",
                        "mastodon_site",
                        "mastodon_token",
                        "mastodon_refresh_token",
                        "mastodon_account",
                    ]
                )
                django_rq.get_queue("mastodon").enqueue(
                    refresh_mastodon_data_task, current_user, token
                )
                messages.add_message(
                    request, messages.INFO, _(f"账号身份已更新为 {username}@{site}。")
                )
    else:
        messages.add_message(request, messages.ERROR, _("连接联邦网络获取身份信息失败。"))
    return redirect(reverse("users:data"))


def auth_login(request, user):
    """Decorates django ``login()``. Attach token to session."""
    auth.login(request, user)
    if (
        user.mastodon_last_refresh < timezone.now() - timedelta(hours=1)
        or user.mastodon_account == {}
    ):
        django_rq.get_queue("mastodon").enqueue(refresh_mastodon_data_task, user)


def auth_logout(request):
    """Decorates django ``logout()``. Release token in session."""
    auth.logout(request)


@login_required
def clear_data(request):
    if request.method == "POST":
        if request.POST.get("verification") == request.user.mastodon_username:
            remove_data_by_user(request.user)
            request.user.first_name = request.user.username
            request.user.last_name = request.user.mastodon_site
            request.user.is_active = False
            request.user.username = "removed_" + str(request.user.id)
            request.user.mastodon_id = 0
            request.user.mastodon_site = "removed"
            request.user.mastodon_token = ""
            request.user.mastodon_locked = False
            request.user.mastodon_followers = []
            request.user.mastodon_following = []
            request.user.mastodon_mutes = []
            request.user.mastodon_blocks = []
            request.user.mastodon_domain_blocks = []
            request.user.mastodon_account = {}
            request.user.save()
            auth_logout(request)
            return redirect(reverse("users:login"))
        else:
            messages.add_message(request, messages.ERROR, _("验证信息不符。"))
    return redirect(reverse("users:data"))
