from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.views import render_error

from ..models import Bluesky
from .common import disconnect_identity, process_verified_account


@require_http_methods(["POST"])
def bluesky_login(request: HttpRequest):
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
