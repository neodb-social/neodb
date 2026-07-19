from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.sentry import count as sentry_count
from common.views import render_error
from users.login_proof import verify_login_proof

from ..models import Bluesky
from .common import client_ip, disconnect_identity, process_verified_account

# Cap failed app-password logins per client IP so the server cannot be used
# to relay credential stuffing against Bluesky accounts.
_MAX_AUTH_FAILS = 10
_AUTH_FAIL_TTL = 60 * 60


@require_http_methods(["POST"])
def bluesky_login(request: HttpRequest):
    if not verify_login_proof(request, "bluesky"):
        return render_error(request, _("Security check failed. Please try again."))
    sentry_count("login.attempt", attributes={"type": "bluesky"})
    fail_key = f"bluesky_login_fails_{client_ip(request)}"
    if (cache.get(fail_key) or 0) >= _MAX_AUTH_FAILS:
        return render_error(
            request,
            _("Authentication failed"),
            _("Too many attempts, please try again later."),
        )
    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    if not username or not password:
        return render_error(
            request,
            _("Authentication failed"),
            _("Username and app password is required."),
        )
    account = Bluesky.authenticate(username, password)
    if not account:
        try:
            cache.incr(fail_key)
        except ValueError:
            cache.set(fail_key, 1, timeout=_AUTH_FAIL_TTL)
        return render_error(
            request, _("Authentication failed"), _("Invalid account data from Bluesky.")
        )
    return process_verified_account(request, account)


@require_http_methods(["POST"])
@login_required
def bluesky_reconnect(request: HttpRequest):
    """link another bluesky to an existing logged-in user"""
    return bluesky_login(request)


@require_http_methods(["POST"])
@login_required
def bluesky_disconnect(request):
    """unlink bluesky from an existing logged-in user"""
    return disconnect_identity(request, request.user.bluesky)
