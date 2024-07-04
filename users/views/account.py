from urllib.parse import quote

import django_rq
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.utils import AuthedHttpRequest
from journal.models import remove_data_by_user
from mastodon.models import Email, Mastodon
from mastodon.models.common import Platform, SocialAccount
from mastodon.models.email import EmailAccount
from takahe.utils import Takahe

from ..models import User


@require_http_methods(["GET"])
def login(request):
    """show login page"""
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


@login_required
def logout(request):
    return auth_logout(request)


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
def register(request: AuthedHttpRequest):
    """show registration page and process the submission from it"""

    # check invite code if invite-only
    if settings.INVITE_ONLY and not request.user.is_authenticated:
        if not Takahe.verify_invite(str(request.session.get("invite"))):
            return render(
                request,
                "common/error.html",
                {
                    "msg": _("Authentication failed"),
                    "secondary_msg": _("Registration is for invitation only"),
                },
            )

    data = request.POST.copy()
    error = None
    if request.user.is_authenticated:
        # logged in user to change email
        verified_account = None
    else:
        verified_account = SocialAccount.from_dict(
            request.session.get("verified_account")
        )
        if not verified_account:
            # kick back to login if no identity verified
            return redirect(reverse("users:login"))

    # no registration form for closed community mode
    if not settings.MASTODON_ALLOW_ANY_SITE:
        if verified_account and verified_account.platform == Platform.MASTODON:
            # directly create a new user
            new_user = User.register(
                account=verified_account,
                username=verified_account.username,  # type:ignore
            )
            auth_login(request, new_user)
            return render(request, "users/welcome.html")
        else:
            return redirect(reverse("common:home"))

    # use verified email if presents for new account creation
    if verified_account and verified_account.platform == Platform.EMAIL:
        data["email"] = verified_account.handle
        email_readonly = True
    else:
        email_readonly = False
    form = RegistrationForm(
        data,
        instance=(
            User.objects.get(pk=request.user.pk)
            if request.user.is_authenticated
            else None
        ),
    )

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
            # new user to finalize registration process
            username = form.cleaned_data["username"]
            if not username:
                error = _("Valid username required")
            elif User.objects.filter(username__iexact=username).exists():
                error = _("Username in use")
            else:
                # all good, create new user
                new_user = User.register(username=username, account=verified_account)
                auth_login(request, new_user)

                if not email_readonly and form.cleaned_data["email"]:
                    # if new user wants to link email too
                    request.session["new_user"] = 1
                    Email.send_login_email(
                        request, form.cleaned_data["email"], "verify"
                    )
                    return render(request, "users/verify.html")
                return render(request, "users/welcome.html")

    return render(
        request,
        "users/register.html",
        {"form": form, "email_readonly": email_readonly, "error": error},
    )


def clear_preference_cache(request):
    for key in list(request.session.keys()):
        if key.startswith("p_"):
            del request.session[key]


def auth_login(request, user):
    auth.login(request, user, backend="mastodon.auth.OAuth2Backend")
    request.session.pop("verified_account", None)
    request.session.pop("invite", None)
    clear_preference_cache(request)


def logout_takahe(response: HttpResponse):
    response.delete_cookie(settings.TAKAHE_SESSION_COOKIE_NAME)
    return response


def auth_logout(request):
    auth.logout(request)
    return logout_takahe(redirect("/"))


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
        v = request.POST.get("verification", "").strip()
        if v:
            for acct in request.user.social_accounts.all():
                if acct.handle == v:
                    django_rq.get_queue("mastodon").enqueue(
                        clear_data_task, request.user.id
                    )
                    messages.add_message(
                        request, messages.INFO, _("Account is being deleted.")
                    )
                    return auth_logout(request)
        messages.add_message(request, messages.ERROR, _("Account mismatch."))
    return redirect(reverse("users:data"))
