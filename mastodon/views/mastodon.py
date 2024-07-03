from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.views import render_error
from mastodon.models import Mastodon
from mastodon.views.common import disconnect_identity, process_verified_account


@require_http_methods(["GET", "POST"])
def mastodon_login(request):
    """verify mastodon api server and redirect"""
    login_domain = request.POST.get("domain") or request.GET.get("domain")
    if not login_domain:
        return render_error(request, _("Missing instance domain"))
    login_domain = (
        login_domain.strip().lower().split("//")[-1].split("/")[0].split("@")[-1]
    )
    try:
        login_url = Mastodon.generate_auth_url(login_domain, request)
        return redirect(login_url)
    except Exception as e:
        return render_error(
            request, _("Error connecting to instance"), f"{login_domain} {e}"
        )


@require_http_methods(["GET"])
def mastodon_oauth(request):
    """handle redirect back from mastodon api server"""
    code = request.GET.get("code")
    if not code:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid response from Fediverse instance."),
        )
    site = request.session.get("mastodon_domain")
    if not site:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid cookie data."),
        )
    try:
        token, refresh_token = Mastodon.obtain_token(site, code, request)
    except ObjectDoesNotExist:
        raise BadRequest(_("Invalid instance domain"))
    if not token:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid token from Fediverse instance."),
        )
    account = Mastodon.authenticate(site, token, refresh_token)
    if not account:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid account data from Fediverse instance."),
        )
    return process_verified_account(request, account)


@login_required
@require_http_methods(["POST"])
def mastodon_reconnect(request):
    """relink to another mastodon from an existing logged-in user"""
    if request.META.get("HTTP_AUTHORIZATION"):
        raise BadRequest("Only for web login")
    return mastodon_login(request)


@require_http_methods(["POST"])
@login_required
def mastodon_disconnect(request):
    """unlink mastodon from an existing logged-in user"""
    return disconnect_identity(request, request.user.mastodon)
