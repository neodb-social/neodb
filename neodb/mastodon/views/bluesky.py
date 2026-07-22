from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.models import SiteConfig
from common.sentry import count as sentry_count
from common.views import render_error
from users.login_proof import verify_login_proof

from ..models import Bluesky
from ..models.bluesky_oauth import get_client_metadata
from .common import client_ip, disconnect_identity, process_verified_account

# Cap failed authorization starts per client IP so the server cannot be
# used to relay identity probing against ATProto handles.
_MAX_AUTH_FAILS = 10
_AUTH_FAIL_TTL = 60 * 60


@require_http_methods(["POST"])
def bluesky_login(request: HttpRequest):
    if not request.user.is_authenticated and not SiteConfig.system.enable_login_bluesky:
        return render_error(request, _("Bluesky login is disabled."))
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
    if not username:
        return render_error(
            request, _("Authentication failed"), _("ATProto handle is required.")
        )
    try:
        login_url = Bluesky.generate_auth_url(username, request)
    except Exception as e:
        try:
            cache.incr(fail_key)
        except ValueError:
            cache.set(fail_key, 1, timeout=_AUTH_FAIL_TTL)
        return render_error(
            request, _("Error connecting to your ATProto server"), f"{username}: {e}"
        )
    return redirect(login_url)


@require_http_methods(["GET"])
def bluesky_oauth(request: HttpRequest):
    """handle redirect back from the ATProto authorization server"""
    if not request.user.is_authenticated and not SiteConfig.system.enable_login_bluesky:
        return render_error(request, _("Bluesky login is disabled."))
    if request.GET.get("error"):
        request.session.pop("atproto_oauth", None)
        return render_error(
            request,
            _("Authentication failed"),
            request.GET.get("error_description") or request.GET["error"],
        )
    code = request.GET.get("code")
    state = request.GET.get("state")
    if not code or not state:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid response from ATProto authorization server."),
        )
    account = Bluesky.receive_oauth_code(request, code, state, request.GET.get("iss"))
    if not account:
        return render_error(
            request, _("Authentication failed"), _("Invalid account data from Bluesky.")
        )
    return process_verified_account(request, account)


@require_http_methods(["GET"])
def bluesky_client_metadata(request: HttpRequest):
    """OAuth client metadata document; its public URL is the client_id"""
    return JsonResponse(get_client_metadata())


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
