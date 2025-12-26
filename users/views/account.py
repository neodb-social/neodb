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

from common.utils import AuthedHttpRequest
from mastodon.forms import EmailLoginForm
from mastodon.models import (
    Email,
    EmailAccount,
    Mastodon,
    MastodonAccount,
    Platform,
    SocialAccount,
)
from takahe.utils import Takahe

from ..models import User


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
    email_form = EmailLoginForm() if settings.ENABLE_LOGIN_EMAIL else None
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
            "email_form": email_form,
            "invite_status": invite_status,
        },
    )


@require_http_methods(["POST"])
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


def _handle_email_change(request, form):
    current_email = (
        request.user.email_account.handle if request.user.email_account else None
    )
    if form.cleaned_data["email"] and form.cleaned_data["email"] != current_email:
        Email.send_login_email(request, form.cleaned_data["email"], "verify")
        return render(
            request, "users/verify.html", {"email": form.cleaned_data["email"]}
        )
    return None


def _handle_new_user_registration(request, form, verified_account, email_readonly):
    username = form.cleaned_data["username"]
    pref = {
        "mastodon_default_repost": request.POST.get("pref_default_crosspost")
        is not None,
        "mastodon_boost_enabled": request.POST.get("pref_mastodon_boost_enabled")
        is not None,
        "mastodon_skip_userinfo": request.POST.get("pref_sync_info") is None,
        "mastodon_skip_relationship": request.POST.get("pref_sync_graph") is None,
    }

    # Form validation already checked for username existence
    new_user = User.register(
        username=username, account=verified_account, preference=pref
    )
    auth_login(request, new_user)

    if not email_readonly and form.cleaned_data["email"]:
        # if new user wants to link email too
        request.session["new_user"] = 1
        Email.send_login_email(request, form.cleaned_data["email"], "verify")
        return render(request, "users/verify.html")
    return render(request, "users/welcome.html")


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
            mastodon_account: MastodonAccount = verified_account  # type: ignore
            new_user = User.register(
                account=mastodon_account,
                username=mastodon_account.username,
            )
            auth_login(request, new_user)
            return render(request, "users/welcome.html")
        else:
            return redirect(request.session.get("next_url", reverse("common:home")))

    # use verified email if presents for new account creation
    if verified_account and verified_account.platform == Platform.EMAIL:
        data["email"] = verified_account.handle
        email_readonly = True
    else:
        email_readonly = False

    instance = (
        User.objects.get(pk=request.user.pk) if request.user.is_authenticated else None
    )
    form = RegistrationForm(data, instance=instance)

    if request.method == "POST" and form.is_valid():
        if request.user.is_authenticated:
            response = _handle_email_change(request, form)
            if response:
                return response
            # If no email change, render register.html again.
        else:
            # new user to finalize registration process
            if not form.cleaned_data.get("username"):
                error = _("Valid username required")
            else:
                return _handle_new_user_registration(
                    request, form, verified_account, email_readonly
                )

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
    return logout_takahe(redirect(request.GET.get("next", "/")))


def initiate_user_deletion(user):
    # for deletion initiated by local user in neodb:
    # 1. clear user data
    # 2. neodb send DeleteIdentity to Takahe
    # 3. takahe delete identity and send identity_deleted to neodb
    # 4. identity_deleted clear user (if not yet) and identity data
    # for deletion initiated by remote/local identity in takahe:
    # just 3 & 4
    user.clear()
    r = Takahe.request_delete_identity(user.identity.pk)
    if not r:
        django_rq.get_queue("mastodon").enqueue(user.identity.clear)


@require_http_methods(["POST"])
@login_required
def clear_data(request):
    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")
    v = request.POST.get("verification", "").strip()
    if v:
        for acct in request.user.social_accounts.all():
            if acct.handle == v:
                initiate_user_deletion(request.user)
                messages.add_message(
                    request, messages.INFO, _("Account is being deleted.")
                )
                return auth_logout(request)
    messages.add_message(request, messages.ERROR, _("Account mismatch."))
    return redirect(reverse("users:data"))
