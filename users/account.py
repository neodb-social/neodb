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
from django.core.signing import TimestampSigner, b62_decode, b62_encode
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
require_http_methods(["GET"])


def login(request):
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


# connect will send verification email or redirect to mastodon server
def connect(request):
    if request.method == "POST" and request.POST.get("method") == "email":
        login_email = request.POST.get("email", "")
        try:
            EmailValidator()(login_email)
        except Exception:
            return render(
                request,
                "common/error.html",
                {"msg": _("Invalid email address")},
            )
        user = User.objects.filter(email__iexact=login_email).first()
        code = b62_encode(random.randint(pow(62, 4), pow(62, 5) - 1))
        cache.set(f"login_{code}", login_email, timeout=60 * 15)
        request.session["login_email"] = login_email
        action = "login" if user else "register"
        django_rq.get_queue("mastodon").enqueue(
            send_verification_link,
            user.pk if user else 0,
            action,
            login_email,
            code,
        )
        return render(
            request,
            "common/verify.html",
            {
                "msg": _("Verification"),
                "secondary_msg": _(
                    "Verification email is being sent, please check your inbox."
                ),
                "action": action,
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
                "msg": _("Missing instance domain"),
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
        request.session["mastodon_domain"] = app.domain_name
        resp = redirect(login_url)
        resp.set_cookie("mastodon_domain", app.domain_name)
        return resp
    except Exception as e:
        return render(
            request,
            "common/error.html",
            {
                "msg": _("Error connecting to instance"),
                "secondary_msg": f"{login_domain} {e}",
            },
        )


# mastodon server redirect back to here
@require_http_methods(["GET"])
@mastodon_request_included
def connect_redirect_back(request):
    code = request.GET.get("code")
    if not code:
        return render(
            request,
            "common/error.html",
            {
                "msg": _("Authentication failed"),
                "secondary_msg": _("Invalid response from Fediverse instance."),
            },
        )
    site = request.session.get("mastodon_domain")
    if not site:
        return render(
            request,
            "common/error.html",
            {
                "msg": _("Authentication failed"),
                "secondary_msg": _("Invalid cookie data."),
            },
        )
    try:
        token, refresh_token = obtain_token(site, request, code)
    except ObjectDoesNotExist:
        raise BadRequest(_("Invalid instance domain"))
    if not token:
        return render(
            request,
            "common/error.html",
            {
                "msg": _("Authentication failed"),
                "secondary_msg": _("Invalid token from Fediverse instance."),
            },
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
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Invalid account data from Fediverse instance."),
                },
            )
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
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Registration is for invitation only"),
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
    # revoke_token(request.user.mastodon_site, request.user.mastodon_token)
    return auth_logout(request)


@mastodon_request_included
@login_required
@require_http_methods(["POST"])
def reconnect(request):
    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")
    request.session["swap_login"] = True
    request.session["swap_domain"] = request.POST["domain"]
    return connect(request)


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


def send_verification_link(user_id, action, email, code=""):
    s = {"i": user_id, "e": email, "a": action}
    v = TimestampSigner().sign_object(s)
    footer = _(
        "\n\nIf you did not mean to register or login, please ignore this email. If you are concerned with your account security, please change the email linked with your account, or contact us."
    )
    site = settings.SITE_INFO["site_name"]
    if action == "verify":
        subject = f'{site} - {_("Verification")}'
        url = settings.SITE_INFO["site_url"] + "/account/verify_email?c=" + v
        msg = _("Click this link to verify your email address {email}\n{url}").format(
            email=email, url=url, code=code
        )
        msg += footer
    elif action == "login":
        subject = f'{site} - {_("Login")} {code}'
        url = settings.SITE_INFO["site_url"] + "/account/login/email?c=" + v
        msg = _(
            "Use this code to confirm login as {email}\n\n{code}\n\nOr click this link to login\n{url}"
        ).format(email=email, url=url, code=code)
        msg += footer
    elif action == "register":
        subject = f'{site} - {_("Register")}'
        url = settings.SITE_INFO["site_url"] + "/account/register_email?c=" + v
        msg = _(
            "There is no account registered with this email address yet.{email}\n\nIf you already have an account with a Fediverse identity, just login and add this email to you account.\n\n"
        ).format(email=email, url=url, code=code)
        if settings.ALLOW_EMAIL_ONLY_ACCOUNT:
            msg += _(
                "\nIf you prefer to register a new account, please use this code: {code}\nOr click this link:\n{url}"
            ).format(email=email, url=url, code=code)
        msg += footer
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
        logger.error(f"send email {email} failed", extra={"exception": e})


@require_http_methods(["POST"])
def verify_code(request):
    code = request.POST.get("code")
    if not code:
        return render(
            request,
            "common/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    login_email = cache.get(f"login_{code}")
    if not login_email or request.session.get("login_email") != login_email:
        return render(
            request,
            "common/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    cache.delete(f"login_{code}")
    user = User.objects.filter(email__iexact=login_email).first()
    if user:
        resp = login_existing_user(request, user)
    else:
        resp = register_new_user(request, username=None, email=login_email)
    resp.set_cookie("mastodon_domain", "@")
    return resp


def verify_email(request):
    error = ""
    try:
        s = TimestampSigner().unsign_object(request.GET.get("c"), max_age=60 * 15)
    except Exception as e:
        logger.warning(f"login link invalid {e}")
        error = _("Invalid verification link")
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
                error = _("Email mismatch")
        elif action == "login":
            user = User.objects.get(pk=s["i"])
            if user.email == email:
                return login_existing_user(request, user)
            else:
                error = _("Email mismatch")
        elif action == "register":
            user = User.objects.filter(email__iexact=email).first()
            if user:
                error = _("Email in use")
            else:
                return register_new_user(request, username=None, email=email)
    except Exception as e:
        logger.error("verify email error", extra={"exception": e, "s": s})
        error = _("Unable to verify")
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
                        "error": _("Username in use"),
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
                            "error": _("Email in use"),
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
            messages.add_message(
                request,
                messages.INFO,
                _("Verification email is being sent, please check your inbox."),
            )
        if request.user.username and not request.user.identity_linked():
            request.user.initialize()
        if username_changed:
            messages.add_message(request, messages.INFO, _("Username all set."))
        if email_cleared:
            messages.add_message(
                request, messages.INFO, _("Email removed from account.")
            )
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
                request,
                messages.ERROR,
                _("Unable to update login information: identical identity."),
            )
        else:
            try:
                User.objects.get(
                    mastodon_username__iexact=username, mastodon_site__iexact=site
                )
                messages.add_message(
                    request,
                    messages.ERROR,
                    _("Unable to update login information: identity in use."),
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
                    request,
                    messages.INFO,
                    _("Login information updated.") + f" {username}@{site}",
                )
    else:
        messages.add_message(
            request, messages.ERROR, _("Invalid account data from Fediverse instance.")
        )
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
            messages.add_message(request, messages.ERROR, _("Account mismatch."))
    return redirect(reverse("users:data"))
