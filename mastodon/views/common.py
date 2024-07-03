from django.contrib import messages
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from common.views import render_error
from mastodon.models.common import SocialAccount
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
    user = authenticate(request, social_account=account)
    if not user:
        return render_error(_("Authentication failed"), _("Invalid user."))
    existing_user = account.user
    auth_login(request, existing_user)
    account.sync_later()
    if not existing_user.username or not existing_user.identity:
        # this should not happen
        response = redirect(reverse("users:register"))
    else:
        response = redirect(request.session.get("next_url", reverse("common:home")))
        request.session.pop("next_url", None)
    return logout_takahe(response)


def register_new_user(request: HttpRequest, account: SocialAccount):
    if request.user.is_authenticated:
        return render_error(_("Registration failed"), _("User already logged in."))
    request.session["verified_account"] = account.to_dict()
    return redirect(reverse("users:register"))


def reconnect_account(request, account: SocialAccount):
    if account.user == request.user:
        return render_error(
            request, _("Unable to update login information: identical identity.")
        )
    elif account.user:
        return render_error(
            request, _("Unable to update login information: identity in use.")
        )
    else:
        # TODO add confirmation screen
        request.user.reconnect_account(account)
        if request.session.get("new_user", 0):
            # new user finishes linking email
            del request.session["new_user"]
            return render(request, "users/welcome.html")
        else:
            account.sync_later()
            messages.add_message(
                request,
                messages.INFO,
                _("Login information updated.") + account.handle,
            )
            return redirect(reverse("users:info"))


def disconnect_identity(request, account):
    if not account:
        return render_error(_("Disconnect identity failed"), _("Identity not found."))
    if request.user != account.user:
        return render_error(_("Disconnect identity failed"), _("Invalid user."))
    with transaction.atomic():
        if request.user.social_accounts.all().count() <= 1:
            return render_error(
                _("Disconnect identity failed"),
                _("You cannot disconnect last login identity."),
            )
        account.delete()
    return redirect(reverse("users:info"))
