from django.contrib import messages
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from common.views import render_error
from mastodon.models.common import SocialAccount
from users.models import User
from users.views.account import auth_login, logout_takahe


def process_verified_account(request: HttpRequest, account: SocialAccount):
    if request.user.is_authenticated:
        # add/update linked identity
        return reconnect_account(request, account)
    if account.user:
        # existing user
        return login_existing_user(request, account)
    else:
        # check invite and ask for username
        return register_new_user(request, account)


def login_existing_user(request: HttpRequest, account: SocialAccount):
    user: User | None = authenticate(request, social_account=account)  # type:ignore
    if not user:
        return render_error(request, _("Authentication failed"), _("Invalid user."))
    existing_user = account.user
    auth_login(request, existing_user)
    user.sync_accounts_later()
    if not existing_user.username or not existing_user.identity:
        # this should not happen
        response = redirect(reverse("users:register"))
    else:
        response = redirect(request.session.get("next_url", reverse("common:home")))
        request.session.pop("next_url", None)
    return logout_takahe(response)


def register_new_user(request: HttpRequest, account: SocialAccount):
    if request.user.is_authenticated:
        return render_error(
            request, _("Registration failed"), _("User already logged in.")
        )
    request.session["verified_account"] = account.to_dict()
    return redirect(reverse("users:register"))


def reconnect_account(request, account: SocialAccount):
    if account.user == request.user:
        account.user.sync_accounts_later()
        messages.add_message(
            request,
            messages.INFO,
            _("Continue login as {handle}.").format(handle=account.handle),
        )
        return redirect(reverse("users:info"))
    elif account.user:
        return render_error(
            request,
            _("Unable to update login information"),
            _("Identity {handle} in use by a different user.").format(
                handle=account.handle
            ),
        )
    else:
        # TODO add confirmation screen
        request.user.reconnect_account(account)
        if request.session.get("new_user", 0):
            # new user finishes linking email
            del request.session["new_user"]
            return render(request, "users/welcome.html")
        else:
            request.user.sync_accounts_later()
            messages.add_message(
                request,
                messages.INFO,
                _("Login information updated as {handle}.").format(
                    handle=account.handle
                ),
            )
            return redirect(reverse("users:info"))


def disconnect_identity(request, account):
    if not account:
        return render_error(
            request, _("Disconnect identity failed"), _("Identity not found.")
        )
    if request.user != account.user:
        return render_error(
            request, _("Disconnect identity failed"), _("Invalid user.")
        )
    with transaction.atomic():
        if request.user.social_accounts.all().count() <= 1:
            return render_error(
                request,
                _("Disconnect identity failed"),
                _("You cannot disconnect last login identity."),
            )
        account.delete()
    messages.add_message(
        request,
        messages.INFO,
        _("Login information about {handle} has been removed.").format(
            handle=account.handle
        ),
    )
    return redirect(reverse("users:info"))
