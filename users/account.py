from datetime import timedelta
from urllib.parse import quote

import django_rq
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import BadRequest, ObjectDoesNotExist
from django.core.mail import send_mail
from django.core.signing import TimestampSigner
from django.core.validators import EmailValidator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.config import *
from common.utils import AuthedHttpRequest
from journal.models import remove_data_by_user
from mastodon import mastodon_request_included
from mastodon.api import *
from mastodon.api import verify_account
from takahe.utils import Takahe

from .models import Preference, User
from .tasks import *


# the 'login' page that user can see
def login(request):
    if request.method == "GET":
        selected_site = request.GET.get("site", default="")

        cache_key = "login_sites"
        sites = cache.get(cache_key, [])
        if not sites:
            sites = list(
                User.objects.filter(is_active=True)
                .values("mastodon_site")
                .annotate(total=Count("mastodon_site"))
                .order_by("-total")
                .values_list("mastodon_site", flat=True)
            )
            cache.set(cache_key, sites, timeout=3600 * 8)
        # store redirect url in the cookie
        if request.GET.get("next"):
            request.session["next_url"] = request.GET.get("next")
        invite_status = -1 if settings.INVITE_ONLY else 0
        if settings.INVITE_ONLY and request.GET.get("invite"):
            if Takahe.verify_invite(request.GET.get("invite")):
                invite_status = 1
                request.session["invite"] = request.GET.get("invite")
            else:
                invite_status = -2
        return render(
            request,
            "users/login.html",
            {
                "sites": sites,
                "scope": quote(settings.MASTODON_CLIENT_SCOPE),
                "selected_site": selected_site,
                "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
                "invite_status": invite_status,
            },
        )
    else:
        raise BadRequest()


# connect will send verification email or redirect to mastodon server
def connect(request):
    if request.method == "POST" and request.POST.get("method") == "email":
        login_email = request.POST.get("email", "")
        try:
            EmailValidator()(login_email)
        except:
            return render(
                request,
                "common/error.html",
                {"msg": _("无效的电子邮件地址")},
            )
        user = User.objects.filter(email__iexact=login_email).first()
        django_rq.get_queue("mastodon").enqueue(
            send_verification_link,
            user.pk if user else 0,
            "login" if user else "register",
            login_email,
        )
        return render(
            request,
            "common/info.html",
            {
                "msg": _("验证邮件已发送"),
                "secondary_msg": _("请查阅收件箱"),
            },
        )
    login_domain = (
        request.session["swap_domain"]
        if request.session.get("swap_login")
        else (request.POST.get("domain") or request.GET.get("domain"))
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
    try:
        app = get_or_create_fediverse_application(login_domain)
        if app.api_domain and app.api_domain != app.domain_name:
            login_domain = app.api_domain
        login_url = get_mastodon_login_url(app, login_domain, request)
        resp = redirect(login_url)
        resp.set_cookie("mastodon_domain", app.domain_name)
        return resp
    except Exception as e:
        return render(
            request,
            "common/error.html",
            {
                "msg": "无法连接指定实例，请检查域名拼写",
                "secondary_msg": str(e),
            },
        )


# mastodon server redirect back to here
@require_http_methods(["GET"])
@mastodon_request_included
def OAuth2_login(request):
    code = request.GET.get("code")
    if not code:
        return render(
            request,
            "common/error.html",
            {"msg": _("认证失败😫"), "secondary_msg": _("Mastodon服务未能返回有效认证信息")},
        )
    site = request.COOKIES.get("mastodon_domain")
    if not site:
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

    user: User = authenticate(request, token=token, site=site)  # type: ignore
    if user:  # existing user
        user.mastodon_token = token
        user.mastodon_refresh_token = refresh_token
        user.save(update_fields=["mastodon_token", "mastodon_refresh_token"])
        return login_existing_user(request, user)
    else:  # newly registered user
        code, user_data = verify_account(site, token)
        if code != 200 or user_data is None:
            return render(request, "common/error.html", {"msg": _("联邦宇宙访问失败😫")})
        return register_new_user(
            request,
            username=None
            if settings.MASTODON_ALLOW_ANY_SITE
            else user_data["username"],
            mastodon_username=user_data["username"],
            mastodon_id=user_data["id"],
            mastodon_site=site,
            mastodon_token=token,
            mastodon_refresh_token=refresh_token,
            mastodon_account=user_data,
        )


def register_new_user(request, **param):
    if settings.INVITE_ONLY:
        if not Takahe.verify_invite(request.session.get("invite")):
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("注册失败😫"),
                    "secondary_msg": _("本站仅限邀请注册"),
                },
            )
        else:
            del request.session["invite"]
    new_user = User.register(**param)
    request.session["new_user"] = True
    auth_login(request, new_user)
    response = redirect(reverse("users:register"))
    response.delete_cookie(settings.TAKAHE_SESSION_COOKIE_NAME)
    return response


def login_existing_user(request, existing_user):
    auth_login(request, existing_user)
    if not existing_user.username or not existing_user.identity:
        response = redirect(reverse("users:register"))
    elif request.session.get("next_url") is not None:
        response = redirect(request.session.get("next_url"))
        del request.session["next_url"]
    else:
        response = redirect(reverse("common:home"))
    response.delete_cookie(settings.TAKAHE_SESSION_COOKIE_NAME)
    return response


@mastodon_request_included
@login_required
def logout(request):
    if request.method == "GET":
        # revoke_token(request.user.mastodon_site, request.user.mastodon_token)
        return auth_logout(request)
    else:
        raise BadRequest()


@mastodon_request_included
@login_required
def reconnect(request):
    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")
    if request.method == "POST":
        request.session["swap_login"] = True
        request.session["swap_domain"] = request.POST["domain"]
        return connect(request)
    else:
        raise BadRequest()


class RegistrationForm(forms.ModelForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ["username"]

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if username and self.instance and self.instance.username:
            username = self.instance.username
        elif (
            username
            and User.objects.filter(username__iexact=username)
            .exclude(pk=self.instance.pk if self.instance else -1)
            .exists()
        ):
            raise forms.ValidationError(_("This username is already in use."))
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if (
            email
            and User.objects.filter(email__iexact=email)
            .exclude(pk=self.instance.pk if self.instance else -1)
            .exists()
        ):
            raise forms.ValidationError(_("This email address is already in use."))
        return email


def send_verification_link(user_id, action, email):
    s = {"i": user_id, "e": email, "a": action}
    v = TimestampSigner().sign_object(s)
    if action == "verify":
        subject = f'{settings.SITE_INFO["site_name"]} - {_("验证电子邮件地址")}'
        url = settings.SITE_INFO["site_url"] + "/account/verify_email?c=" + v
        msg = f"你好，\n请点击以下链接验证你的电子邮件地址 {email}\n{url}\n\n如果你没有注册过本站，请忽略此邮件。"
    elif action == "login":
        subject = f'{settings.SITE_INFO["site_name"]} - {_("登录")}'
        url = settings.SITE_INFO["site_url"] + "/account/login/email?c=" + v
        msg = f"你好，\n请点击以下链接登录{email}账号\n{url}\n\n如果你没有请求登录本站，请忽略此邮件；如果你确信账号存在安全风险，请更改注册邮件地址或与我们联系。"
    elif action == "register":
        subject = f'{settings.SITE_INFO["site_name"]} - {_("注册新账号")}'
        url = settings.SITE_INFO["site_url"] + "/account/register_email?c=" + v
        msg = f"你好，\n本站没有与{email}关联的账号。你希望注册一个新账号吗？\n"
        msg += f"\n如果你已注册过本站或某个联邦宇宙（长毛象）实例，不必重新注册，只要用联邦宇宙身份登录本站，再关联这个电子邮件地址，即可通过邮件登录。\n"
        msg += f"\n如果你还没有联邦宇宙身份，可以访问这里选择实例并创建一个： https://joinmastodon.org/zh/servers\n"
        if settings.ALLOW_EMAIL_ONLY_ACCOUNT:
            msg += f"\n如果你不便使用联邦宇宙身份，也可以点击以下链接使用电子邮件注册一个新账号，以后再关联到联邦宇宙。\n{url}\n"
        msg += f"\n如果你没有打算用此电子邮件地址注册或登录本站，请忽略此邮件。"
    else:
        raise ValueError("Invalid action")
    try:
        logger.info(f"Sending email to {email} with subject {subject}")
        logger.debug(msg)
        send_mail(
            subject=subject,
            message=msg,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as e:
        logger.error(e)


def verify_email(request):
    error = ""
    try:
        s = TimestampSigner().unsign_object(request.GET.get("c"), max_age=60 * 15)
    except Exception as e:
        logger.warning(f"login link invalid {e}")
        error = _("链接无效或已过期")
        return render(
            request, "users/verify_email.html", {"success": False, "error": error}
        )
    try:
        email = s["e"]
        action = s["a"]
        if action == "verify":
            user = User.objects.get(pk=s["i"])
            if user.pending_email == email:
                user.email = user.pending_email
                user.pending_email = None
                user.save(update_fields=["email", "pending_email"])
                return render(
                    request, "users/verify_email.html", {"success": True, "user": user}
                )
            else:
                error = _("电子邮件地址不匹配")
        elif action == "login":
            user = User.objects.get(pk=s["i"])
            if user.email == email:
                return login_existing_user(request, user)
            else:
                error = _("电子邮件地址不匹配")
        elif action == "register":
            user = User.objects.filter(email__iexact=email).first()
            if user:
                error = _("此电子邮件地址已被注册")
            else:
                return register_new_user(request, username=None, email=email)
    except Exception as e:
        logger.error(e)
        error = _("无法完成验证")
    return render(
        request, "users/verify_email.html", {"success": False, "error": error}
    )


@login_required
def register(request: AuthedHttpRequest):
    form = None
    if settings.MASTODON_ALLOW_ANY_SITE:
        form = RegistrationForm(request.POST)
        form.instance = (
            User.objects.get(pk=request.user.pk)
            if request.user.is_authenticated
            else None
        )
    if request.method == "GET" or not form:
        return render(request, "users/register.html", {"form": form})
    elif request.method == "POST":
        username_changed = False
        email_cleared = False
        if not form.is_valid():
            return render(request, "users/register.html", {"form": form})
        if not request.user.username and form.cleaned_data["username"]:
            if User.objects.filter(
                username__iexact=form.cleaned_data["username"]
            ).exists():
                return render(
                    request,
                    "users/register.html",
                    {
                        "form": form,
                        "error": _("用户名已被使用"),
                    },
                )
            request.user.username = form.cleaned_data["username"]
            username_changed = True
        if form.cleaned_data["email"]:
            if form.cleaned_data["email"].lower() != (request.user.email or "").lower():
                if User.objects.filter(
                    email__iexact=form.cleaned_data["email"]
                ).exists():
                    return render(
                        request,
                        "users/register.html",
                        {
                            "form": form,
                            "error": _("电子邮件地址已被使用"),
                        },
                    )
                request.user.pending_email = form.cleaned_data["email"]
            else:
                request.user.pending_email = None
        elif request.user.email or request.user.pending_email:
            request.user.pending_email = None
            request.user.email = None
            email_cleared = True
        request.user.save()
        if request.user.pending_email:
            django_rq.get_queue("mastodon").enqueue(
                send_verification_link,
                request.user.pk,
                "verify",
                request.user.pending_email,
            )
            messages.add_message(request, messages.INFO, _("已发送验证邮件，请查收。"))
        if request.user.username and not request.user.identity_linked():
            request.user.initialize()
        if username_changed:
            messages.add_message(request, messages.INFO, _("用户名已设置。"))
        if email_cleared:
            messages.add_message(request, messages.INFO, _("电子邮件地址已取消关联。"))
        if request.session.get("new_user"):
            del request.session["new_user"]
    return redirect(request.GET.get("next", reverse("common:home")))


def swap_login(request, token, site, refresh_token):
    del request.session["swap_login"]
    del request.session["swap_domain"]
    code, data = verify_account(site, token)
    current_user = request.user
    if code == 200 and data is not None:
        username = data["username"]
        if (
            username == current_user.mastodon_username
            and site == current_user.mastodon_site
        ):
            messages.add_message(
                request, messages.ERROR, _(f"该身份 {username}@{site} 与当前账号相同。")
            )
        else:
            try:
                existing_user = User.objects.get(
                    mastodon_username__iexact=username, mastodon_site__iexact=site
                )
                messages.add_message(
                    request, messages.ERROR, _(f"该身份 {username}@{site} 已被用于其它账号。")
                )
            except ObjectDoesNotExist:
                current_user.mastodon_username = username
                current_user.mastodon_id = data["id"]
                current_user.mastodon_site = site
                current_user.mastodon_token = token
                current_user.mastodon_refresh_token = refresh_token
                current_user.mastodon_account = data
                current_user.save(
                    update_fields=[
                        "username",
                        "mastodon_id",
                        "mastodon_username",
                        "mastodon_site",
                        "mastodon_token",
                        "mastodon_refresh_token",
                        "mastodon_account",
                    ]
                )
                django_rq.get_queue("mastodon").enqueue(
                    refresh_mastodon_data_task, current_user.pk, token
                )
                messages.add_message(
                    request, messages.INFO, _(f"账号身份已更新为 {username}@{site}。")
                )
    else:
        messages.add_message(request, messages.ERROR, _("连接联邦宇宙获取身份信息失败。"))
    return redirect(reverse("users:data"))


def clear_preference_cache(request):
    for key in list(request.session.keys()):
        if key.startswith("p_"):
            del request.session[key]


def auth_login(request, user):
    """Decorates django ``login()``. Attach token to session."""
    auth.login(request, user, backend="mastodon.auth.OAuth2Backend")
    clear_preference_cache(request)
    if (
        user.mastodon_last_refresh < timezone.now() - timedelta(hours=1)
        or user.mastodon_account == {}
    ):
        django_rq.get_queue("mastodon").enqueue(refresh_mastodon_data_task, user.pk)


def auth_logout(request):
    """Decorates django ``logout()``. Release token in session."""
    auth.logout(request)
    response = redirect("/")
    response.delete_cookie(settings.TAKAHE_SESSION_COOKIE_NAME)
    return response


def clear_data_task(user_id):
    user = User.objects.get(pk=user_id)
    user_str = str(user)
    if user.identity:
        remove_data_by_user(user.identity)
    Takahe.delete_identity(user.identity.pk)
    user.clear()
    logger.warning(f"User {user_str} data cleared.")


@login_required
def clear_data(request):
    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")
    if request.method == "POST":
        v = request.POST.get("verification")
        if v and (v == request.user.mastodon_acct or v == request.user.email):
            django_rq.get_queue("mastodon").enqueue(clear_data_task, request.user.id)
            return auth_logout(request)
        else:
            messages.add_message(request, messages.ERROR, _("验证信息不符。"))
    return redirect(reverse("users:data"))
