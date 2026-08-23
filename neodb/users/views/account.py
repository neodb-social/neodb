import re
import secrets
from urllib.parse import quote

import django_rq
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import ItemCategory
from common.models import SiteConfig
from common.sentry import record_activity, record_registration_captcha
from common.utils import AuthedHttpRequest, client_ip
from common.validators import sanitize_next_url
from mastodon.models import (
    Email,
    EmailAccount,
    Mastodon,
    MastodonAccount,
    Platform,
    SocialAccount,
)
from takahe.models import Token
from takahe.utils import Takahe

from .. import registration_captcha as captcha
from ..login_proof import LOGIN_PROOF_METHODS, create_login_proof_challenge
from ..models import User


@require_http_methods(["GET"])
def login(request):
    enable_mastodon = SiteConfig.system.enable_login_mastodon
    selected_domain = request.GET.get("domain", default="")
    # "atproto" kept as an alias for "bluesky": reauth URLs using it are
    # persisted in old notification messages
    selected_method = request.GET.get("method", default="")
    if selected_method == "atproto":
        selected_method = "bluesky"
    if selected_method not in ("passkey", "email", "mastodon", "threads", "bluesky"):
        selected_method = ""
    if not enable_mastodon:
        selected_domain = ""
        if selected_method == "mastodon":
            selected_method = ""
    # ATProto handle to prefill the Bluesky form, e.g. from a reauth link
    selected_username = request.GET.get("username", default="")
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", selected_username):
        selected_username = ""
    sites = Mastodon.get_sites() if enable_mastodon else []
    next_url = sanitize_next_url(request.GET.get("next"))
    if next_url:
        request.session["next_url"] = next_url
    invite_status = -1 if SiteConfig.system.invite_only else 0
    if SiteConfig.system.invite_only and request.GET.get("invite"):
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
            "scope": quote(SiteConfig.system.mastodon_client_scope),
            "selected_domain": selected_domain,
            "selected_method": selected_method,
            "selected_username": selected_username,
            "allow_any_site": not enable_mastodon
            or len(SiteConfig.system.mastodon_login_whitelist) == 0,
            "enable_mastodon": enable_mastodon,
            "enable_email": settings.ENABLE_LOGIN_EMAIL,
            "enable_threads": SiteConfig.system.enable_login_threads,
            "enable_bluesky": SiteConfig.system.enable_login_bluesky,
            "invite_status": invite_status,
        },
    )


@require_http_methods(["GET"])
def login_proof(request: HttpRequest) -> JsonResponse:
    method = request.GET.get("method", "")
    if method not in LOGIN_PROOF_METHODS:
        return JsonResponse({"error": "Unknown login method"}, status=400)
    if method == "mastodon" and not SiteConfig.system.enable_login_mastodon:
        return JsonResponse({"error": "Mastodon login is disabled"}, status=400)
    response = JsonResponse(create_login_proof_challenge(request, method))
    response["Cache-Control"] = "no-store, private"
    return response


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
        # the checkbox is only offered when registering with a Bluesky account
        "bluesky_publish_records": verified_account.platform == Platform.BLUESKY
        and request.POST.get("pref_bluesky_publish_records") is not None,
    }

    try:
        new_user = User.register(
            username=username, account=verified_account, preference=pref
        )
    except ValidationError as e:
        form.add_error("username", e.message)
        return None
    auth_login(request, new_user)
    record_activity("register", "web")

    if not email_readonly and form.cleaned_data["email"]:
        # if new user wants to link email too
        request.session["new_user"] = 1
        Email.send_login_email(request, form.cleaned_data["email"], "verify")
        return render(request, "users/verify.html")
    return render(request, "users/welcome.html")


def _captcha_context(request: HttpRequest, challenge) -> dict:
    tokens = list(challenge["tiles"].keys())
    # the stored order is already shuffled; reshuffle per render so a reload
    # cannot be diffed against the first response to infer the grouping
    secrets.SystemRandom().shuffle(tokens)
    return {
        "tokens": tokens,
        "rows": [
            {"index": i, "value": v, "label": ItemCategory(v).label}
            for i, v in enumerate(challenge["rows"])
        ],
        "nonce": challenge["nonce"],
        "seconds_left": captcha.seconds_left(challenge),
        "max_trace_points": captcha.CAPTCHA_MAX_TRACE_POINTS,
        "regenerations_left": captcha.regenerations_left(challenge),
        "msg": captcha.pop_message(request),
    }


def _render_error(request: HttpRequest, title, message=""):
    # common.views.render_error would be the natural call, but importing it
    # here is circular: common.views imports catalog.views, which imports this
    # module. register() renders the same template inline for the same reason.
    return render(
        request, "common/error.html", {"msg": title, "secondary_msg": message}
    )


def _captcha_end(request: HttpRequest, outcome: str, title, message):
    """Drop the pending registration and send the visitor back to login.

    There is no logged-in user during registration, so "logged out" means
    flushing the session: `verified_account` goes with it, and the provider
    round trip has to be repeated. auth_logout() is the wrong tool here, it
    reads ?next off a GET and clears a Takahe cookie an anonymous visitor
    does not have.
    """
    record_registration_captcha(outcome)
    request.session.flush()
    return _render_error(request, title, message)


def _captcha_fail_open(request: HttpRequest):
    """Let registration through when no fair quiz can be built."""
    captcha.mark_passed(request)
    record_registration_captcha("fail_open")
    return redirect(reverse("users:register"))


@require_http_methods(["GET", "POST"])
def registration_captcha(request: HttpRequest):
    """Sort item covers into two category rows before choosing a username."""
    if request.user.is_authenticated:
        return redirect(reverse("users:info"))
    if not SocialAccount.from_dict(request.session.get("verified_account")):
        return redirect(reverse("users:login"))
    if not captcha.is_enabled():
        return redirect(reverse("users:register"))
    if captcha.has_passed(request):
        # the moment the answer is accepted, shown once on the page they were
        # working on rather than as a silent redirect
        if captcha.pop_celebration(request):
            return render(request, "users/captcha_solved.html")
        return redirect(reverse("users:register"))
    # mirror register()'s invite gate: without it an uninvited visitor can
    # solve quizzes on an invite-only site before being turned away anyway
    if SiteConfig.system.invite_only and not Takahe.verify_invite(
        str(request.session.get("invite"))
    ):
        return _render_error(
            request,
            _("Authentication failed"),
            _("Registration is for invitation only"),
        )

    if request.method == "POST":
        challenge = captcha.get_challenge(request)
        if not challenge:
            return redirect(reverse("users:captcha"))
        if captcha.expired(challenge):
            return _captcha_end(
                request,
                "expired",
                _("Time is up"),
                _("Please log in again to continue registration."),
            )
        # A stale form -- re-post, back button, double click -- costs nothing
        # and just re-renders whatever is current.
        if request.POST.get("nonce") != challenge["nonce"]:
            return redirect(reverse("users:captcha"))
        # Then claim it atomically. Comparing the nonce and only rotating it in
        # a later session write would let concurrent posts all pass and collapse
        # into a single spent regeneration, which is an unlimited-guess hole.
        # Claiming after the match check keeps junk nonces out of the cache.
        if not captcha.claim_nonce(challenge["nonce"]):
            return redirect(reverse("users:captcha"))

        if request.POST.get("action") == "regenerate":
            outcome = captcha.regenerate(request)
            if outcome["exhausted"]:
                # no-op rather than session-ending: a stray click on a button
                # that should already be hidden must not cost a signup
                return redirect(reverse("users:captcha"))
            if not outcome["challenge"]:
                return _captcha_fail_open(request)
            record_registration_captcha("regenerated")
            return redirect(reverse("users:captcha"))

        ok, outcome_name, reason = captcha.verify_submission(
            challenge,
            request.POST.get("answer", ""),
            request.POST.get("trace", ""),
        )
        if ok:
            captcha.mark_passed(request, celebrate=True)
            record_registration_captcha("passed")
            return redirect(reverse("users:captcha"))
        record_registration_captcha(outcome_name, reason)
        ip = client_ip(request)
        captcha.record_fail(ip)
        # enforce the cap here too, not only when issuing: otherwise a live
        # challenge keeps serving attempts long past the limit
        if captcha.regenerations_left(challenge) <= 0 or captcha.fails_exceeded(ip):
            return _captcha_end(
                request,
                "exhausted",
                _("Verification failed"),
                _("Please log in again to continue registration."),
            )
        regenerated = captcha.regenerate(
            request, msg=_("That is not quite right. Here is a new set.")
        )
        if not regenerated["challenge"] and not regenerated["exhausted"]:
            return _captcha_fail_open(request)
        return redirect(reverse("users:captcha"))

    challenge = captcha.get_challenge(request)
    if not challenge:
        if captcha.fails_exceeded(client_ip(request)):
            record_registration_captcha("rate_limited")
            return _render_error(
                request,
                _("Too many attempts"),
                _("Please try again later."),
            )
        challenge = captcha.ensure_challenge(request)
        if not challenge:
            # the catalog cannot supply a fair quiz right now; letting people
            # register matters more than running the check
            return _captcha_fail_open(request)
        record_registration_captcha("issued")
    elif captcha.expired(challenge):
        return _captcha_end(
            request,
            "expired",
            _("Time is up"),
            _("Please log in again to continue registration."),
        )
    return render(request, "users/captcha.html", _captcha_context(request, challenge))


@require_http_methods(["GET"])
def registration_captcha_tile(request: HttpRequest, token: str):
    """Serve one tile image.

    The proxy exists because cover paths are `item/<category>/...`: linking the
    real cover would spell out the answer. Unknown, stale and foreign tokens
    all 404 identically so the response cannot be used as an oracle.
    """
    resolved = captcha.tile_item(request, token)
    data = captcha.render_tile(*resolved) if resolved else None
    if not data:
        raise Http404
    response = HttpResponse(data, content_type=captcha.CAPTCHA_TILE_CONTENT_TYPE)
    response["Cache-Control"] = "no-store, private"
    response["Vary"] = "Cookie"
    return response


@require_http_methods(["GET", "POST"])
def register(request: AuthedHttpRequest):
    """show registration page and process the submission from it"""

    # check invite code if invite-only
    if SiteConfig.system.invite_only and not request.user.is_authenticated:
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
        if captcha.is_enabled() and not captcha.has_passed(request):
            # the enforcement point, not the redirect in register_new_user: this
            # catches a POST straight to /account/register, and sits above the
            # closed-community branch below, which creates an account outright
            return redirect(reverse("users:captcha"))

    # no registration form for closed community mode
    if (
        SiteConfig.system.enable_login_mastodon
        and not len(SiteConfig.system.mastodon_login_whitelist) == 0
    ):
        if verified_account and verified_account.platform == Platform.MASTODON:
            # directly create a new user
            mastodon_account: MastodonAccount = verified_account
            try:
                new_user = User.register(
                    account=mastodon_account,
                    username=mastodon_account.username,
                )
            except ValidationError:
                return render(
                    request,
                    "common/error.html",
                    {
                        "msg": _("Registration failed"),
                        "secondary_msg": _("Username already taken. Please try again."),
                    },
                )
            auth_login(request, new_user)
            record_activity("register", "web")
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
                response = _handle_new_user_registration(
                    request, form, verified_account, email_readonly
                )
                if response:
                    return response

    return render(
        request,
        "users/register.html",
        {
            "form": form,
            "email_readonly": email_readonly,
            "error": error,
            "bluesky_register": bool(
                verified_account and verified_account.platform == Platform.BLUESKY
            ),
        },
    )


def clear_preference_cache(request):
    for key in list(request.session.keys()):
        if key.startswith("p_"):
            del request.session[key]


def auth_login(request, user):
    auth.login(request, user, backend="mastodon.auth.OAuth2Backend")
    request.session.pop("verified_account", None)
    request.session.pop("invite", None)
    # a solved captcha is worth exactly one account: dropping it here is what
    # keeps the pass from outliving the registration it authorised
    captcha.clear(request)
    clear_preference_cache(request)


def logout_takahe(response: HttpResponse):
    response.delete_cookie(settings.TAKAHE_SESSION_COOKIE_NAME)
    return response


def auth_logout(request):
    auth.logout(request)
    redirect_url = request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        redirect_url = "/"
    return logout_takahe(redirect(redirect_url))


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
                record_activity("leave", "web")
                messages.add_message(
                    request, messages.INFO, _("Account is being deleted.")
                )
                return auth_logout(request)
    messages.add_message(request, messages.ERROR, _("Account mismatch."))
    return redirect(reverse("users:data"))


@require_http_methods(["POST"])
@login_required
def logout_everywhere(request):
    """Log out all sessions and revoke all API tokens."""

    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")

    user = request.user

    # Randomize password to invalidate all sessions (password field is unused in NeoDB)
    user.set_password(secrets.token_urlsafe(32))
    user.save(update_fields=["password"])
    Takahe.sync_password(user)

    # Revoke all API tokens
    identity = getattr(user, "identity", None)
    if identity:
        Token.objects.filter(identity_id=identity.pk, revoked__isnull=True).delete()

    return auth_logout(request)
