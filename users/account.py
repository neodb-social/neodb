from datetime import timedelta
from urllib.parse import quote

import django_rq
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist
from django.core.validators import EmailValidator
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.config import *
from common.utils import AuthedHttpRequest
from journal.models import remove_data_by_user
from mastodon.models import Email, Mastodon
from mastodon.models.common import Platform, SocialAccount
from mastodon.models.email import EmailAccount
from takahe.utils import Takahe

from .models import User
from .tasks import *


@require_http_methods(["GET"])
def login(request):
    selected_domain = request.GET.get("domain", default="")
    sites = Mastodon.get_sites()
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
            "selected_domain": selected_domain,
            "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
            "enable_email": settings.ENABLE_LOGIN_EMAIL,
            "enable_threads": settings.ENABLE_LOGIN_THREADS,
            "enable_bluesky": settings.ENABLE_LOGIN_BLUESKY,
            "invite_status": invite_status,
        },
    )


# connect will send verification email or redirect to mastodon server
@require_http_methods(["GET", "POST"])
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
        Email.send_login_email(request, login_email, "login")
        return render(
            request,
            "users/verify.html",
            {
                "msg": _("Verification"),
                "secondary_msg": _(
                    "Verification email is being sent, please check your inbox."
                ),
                "action": "login",
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
        login_url = Mastodon.generate_auth_url(login_domain, request)
        return redirect(login_url)
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
        token, refresh_token = Mastodon.obtain_token(site, code, request)
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

    if request.session.get("swap_login", False) and request.user.is_authenticated:
        # swap login for existing user
        return swap_login(request, token, site, refresh_token)

    account = Mastodon.authenticate(site, token, refresh_token)
    if not account:
        return render(
            request,
            "common/error.html",
            {
                "msg": _("Authentication failed"),
                "secondary_msg": _("Invalid account data from Fediverse instance."),
            },
        )
    if account.user:  # existing user
        user: User | None = authenticate(request, social_account=account)  # type: ignore
        if not user:
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Invalid user."),
                },
            )
        return login_existing_user(request, user)
    elif not settings.MASTODON_ALLOW_ANY_SITE:  # directly create a new user
        new_user = User.register(
            account=account,
            username=account.username,
        )
        auth_login(request, new_user)
        return render(request, "users/welcome.html")
    else:  # check invite and ask for username
        return register_new_user(request, account)


def register_new_user(request, account: SocialAccount):
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
        del request.session["invite"]
    if request.user.is_authenticated:
        auth.logout(request)
    request.session["verified_account"] = account.to_dict()
    if account.platform == Platform.EMAIL:
        email_readyonly = True
        data = {"email": account.handle}
    else:
        email_readyonly = False
        data = {"email": ""}
    form = RegistrationForm(data)
    return render(
        request,
        "users/register.html",
        {"form": form, "email_readyonly": email_readyonly},
    )


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


@login_required
def logout(request):
    return auth_logout(request)


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
        email = self.cleaned_data.get("email", "").strip()
        if (
            email
            and EmailAccount.objects.filter(handle__iexact=email)
            .exclude(user_id=self.instance.pk if self.instance else -1)
            .exists()
        ):
            raise forms.ValidationError(_("This email address is already in use."))
        return email


@require_http_methods(["GET", "POST"])
def verify_code(request):
    if request.method == "GET":
        return render(request, "users/verify.html")
    code = request.POST.get("code", "").strip()
    if not code:
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    account = Email.authenticate(request, code)
    if not account:
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    if request.user.is_authenticated:
        # existing logged in user to verify a pending email
        if request.user.email_account == account:
            # same email, nothing to do
            return render(request, "users/welcome.html")
        if account.user and account.user != request.user:
            # email used by another user
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Email already in use"),
                },
            )
        with transaction.atomic():
            if request.user.email_account:
                request.user.email_account.delete()
            account.user = request.user
            account.save()
            if request.session.get("new_user", 0):
                try:
                    del request.session["new_user"]
                except KeyError:
                    pass
                return render(request, "users/welcome.html")
            else:
                return redirect(reverse("users:info"))
    if account.user:
        # existing user: log back in
        user = authenticate(request, social_account=account)
        if user:
            return login_existing_user(request, user)
        else:
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Invalid user."),
                },
            )
    # new user: check invite and ask for username
    return register_new_user(request, account)


@require_http_methods(["GET", "POST"])
def register(request: AuthedHttpRequest):
    if not settings.MASTODON_ALLOW_ANY_SITE:
        return render(request, "users/welcome.html")
    form = RegistrationForm(
        request.POST,
        instance=(
            User.objects.get(pk=request.user.pk)
            if request.user.is_authenticated
            else None
        ),
    )
    verified_account = SocialAccount.from_dict(request.session.get("verified_account"))
    email_readonly = (
        verified_account is not None and verified_account.platform == Platform.EMAIL
    )
    error = None
    if request.method == "POST" and form.is_valid():
        if request.user.is_authenticated:
            # logged in user to change email
            current_email = (
                request.user.email_account.handle
                if request.user.email_account
                else None
            )
            if (
                form.cleaned_data["email"]
                and form.cleaned_data["email"] != current_email
            ):
                Email.send_login_email(request, form.cleaned_data["email"], "verify")
                return render(request, "users/verify.html")
        else:
            # new user finishes login process
            if not form.cleaned_data["username"]:
                error = _("Valid username required")
            elif User.objects.filter(
                username__iexact=form.cleaned_data["username"]
            ).exists():
                error = _("Username in use")
            else:
                # create new user
                new_user = User.register(
                    username=form.cleaned_data["username"], account=verified_account
                )
                auth_login(request, new_user)
                if not email_readonly and form.cleaned_data["email"]:
                    # verify email if presented
                    Email.send_login_email(
                        request, form.cleaned_data["email"], "verify"
                    )
                    request.session["new_user"] = 1
                    return render(request, "users/verify.html")
                return render(request, "users/welcome.html")
    return render(
        request,
        "users/register.html",
        {"form": form, "email_readonly": email_readonly, "error": error},
    )


def swap_login(request, token, site, refresh_token):
    del request.session["swap_login"]
    del request.session["swap_domain"]
    account = Mastodon.authenticate(site, token, refresh_token)
    current_user = request.user
    if account:
        if account.user == current_user:
            messages.add_message(
                request,
                messages.ERROR,
                _("Unable to update login information: identical identity."),
            )
        elif account.user:
            messages.add_message(
                request,
                messages.ERROR,
                _("Unable to update login information: identity in use."),
            )
        else:
            with transaction.atomic():
                if current_user.mastodon:
                    current_user.mastodon.delete()
                account.user = current_user
                account.save()
                current_user.mastodon_username = account.username
                current_user.mastodon_id = account.account_data["id"]
                current_user.mastodon_site = account.domain
                current_user.mastodon_token = account.access_token
                current_user.mastodon_refresh_token = account.refresh_token
                current_user.mastodon_account = account.account_data
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
                _("Login information updated.") + account.handle,
            )
    else:
        messages.add_message(
            request, messages.ERROR, _("Invalid account data from Fediverse instance.")
        )
    return redirect(reverse("users:info"))


def clear_preference_cache(request):
    for key in list(request.session.keys()):
        if key.startswith("p_"):
            del request.session[key]


def auth_login(request, user):
    auth.login(request, user, backend="mastodon.auth.OAuth2Backend")
    request.session.pop("verified_account", None)
    clear_preference_cache(request)
    if (
        user.mastodon_last_refresh < timezone.now() - timedelta(hours=1)
        or user.mastodon_account == {}
    ):
        django_rq.get_queue("mastodon").enqueue(refresh_mastodon_data_task, user.pk)


def auth_logout(request):
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
