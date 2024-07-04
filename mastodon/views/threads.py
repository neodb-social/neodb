from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.views import render_error

from ..models import Threads
from .common import disconnect_identity, process_verified_account


@require_http_methods(["POST"])
def threads_login(request: HttpRequest):
    """start login process via threads"""
    return redirect(Threads.generate_auth_url(request))


@require_http_methods(["POST"])
@login_required
def threads_reconnect(request: HttpRequest):
    """link another threads to an existing logged-in user"""
    return redirect(Threads.generate_auth_url(request))


@require_http_methods(["POST"])
@login_required
def threads_disconnect(request):
    """unlink threads from an existing logged-in user"""
    return disconnect_identity(request, request.user.threads)


@require_http_methods(["GET"])
def threads_oauth(request: HttpRequest):
    """handle redirect back from threads"""
    code = request.GET.get("code")
    if not code:
        return render_error(
            request,
            _("Authentication failed"),
            request.GET.get("error_description", ""),
        )
    account = Threads.authenticate(request, code)
    if not account:
        return render_error(
            request, _("Authentication failed"), _("Invalid account data from Threads.")
        )
    return process_verified_account(request, account)


@require_http_methods(["GET"])
def threads_uninstall(request: HttpRequest):
    return redirect(reverse("users:data"))


@require_http_methods(["GET"])
def threads_delete(request: HttpRequest):
    return redirect(reverse("users:data"))
