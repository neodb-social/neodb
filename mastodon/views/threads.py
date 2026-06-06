import base64
import hashlib
import hmac
import json
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models import SiteConfig
from common.sentry import count as sentry_count
from common.views import render_error

from ..models import Threads, ThreadsAccount
from .common import disconnect_identity, process_verified_account


@require_http_methods(["POST"])
def threads_login(request: HttpRequest):
    """start login process via threads"""
    sentry_count("login.attempt", attributes={"type": "threads"})
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
    expected_state = request.session.pop("oauth_state", None)
    actual_state = request.GET.get("state")
    if not expected_state or expected_state != actual_state:
        return render_error(
            request,
            _("Authentication failed"),
            _("Invalid OAuth state. Please try again."),
        )
    account = Threads.authenticate(request, code)
    if not account:
        return render_error(
            request, _("Authentication failed"), _("Invalid account data from Threads.")
        )
    return process_verified_account(request, account)


def _parse_signed_request(signed_request: str) -> dict | None:
    """parse and verify a signed_request POSTed by Meta (base64url, HMAC-SHA256)"""
    if "." not in signed_request:
        return None
    encoded_sig, payload = signed_request.split(".", 1)
    try:
        sig = base64.urlsafe_b64decode(encoded_sig + "=" * (-len(encoded_sig) % 4))
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError, TypeError:
        return None
    secret = SiteConfig.system.threads_app_secret
    if not secret:
        logger.warning("Threads app secret not configured")
        return None
    expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        logger.warning("Threads signed_request signature mismatch")
        return None
    return data if isinstance(data, dict) else None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def threads_uninstall(request: HttpRequest):
    """handle deauthorize callback POSTed by Meta; GET redirects users to data page"""
    if request.method == "GET":
        return redirect(reverse("users:data"))
    data = _parse_signed_request(request.POST.get("signed_request", ""))
    if data is None or not data.get("user_id"):
        return HttpResponse(status=400)
    account = ThreadsAccount.objects.filter(
        uid=str(data["user_id"]), domain=Threads.DOMAIN
    ).first()
    if account:
        logger.info(f"{account} deauthorized via Threads callback")
        account.access_token = ""
        account.token_expires_at = None
        account.save(update_fields=["access_data"])
    return HttpResponse(status=200)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def threads_delete(request: HttpRequest):
    """handle data deletion request POSTed by Meta; GET redirects users to data page"""
    if request.method == "GET":
        return redirect(reverse("users:data"))
    data = _parse_signed_request(request.POST.get("signed_request", ""))
    if data is None or not data.get("user_id"):
        return HttpResponse(status=400)
    uid = str(data["user_id"])
    account = ThreadsAccount.objects.filter(uid=uid, domain=Threads.DOMAIN).first()
    if account:
        logger.info(f"{account} deleted via Threads data deletion callback")
        account.delete()
    confirmation_code = "threads_" + uid
    status_url = (
        request.build_absolute_uri(reverse("mastodon:threads_delete_status"))
        + "?code="
        + confirmation_code
    )
    return JsonResponse({"url": status_url, "confirmation_code": confirmation_code})


@require_http_methods(["GET"])
def threads_delete_status(request: HttpRequest):
    """status page for Threads data deletion requests"""
    # confirmation codes are generated as "threads_<numeric uid>"
    code = re.sub(r"[^A-Za-z0-9_-]", "", request.GET.get("code", ""))
    return render(
        request,
        "common/error.html",
        {
            "msg": _("Data deletion completed"),
            "secondary_msg": _(
                "Data linked to your Threads account has been deleted. Confirmation code: {code}"
            ).format(code=code),
        },
    )
