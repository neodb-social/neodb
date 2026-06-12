from django.core.cache import cache
from django.core.validators import EmailValidator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.sentry import count as sentry_count
from common.views import render_error

from ..forms import EmailLoginForm
from ..models import Email
from .common import client_ip, process_verified_account

# Cap failed verification-code submissions per client IP to defeat brute force.
_MAX_VERIFY_FAILS = 10
_VERIFY_FAIL_TTL = 60 * 60


@require_http_methods(["GET"])
def email_login_state(request):
    email = request.GET.get("email", "")
    state = "error"
    if email and "@" in email:
        state = Email.get_login_state(email) or "error"
    resp = HttpResponse(state)
    if state != "pending":
        resp.status_code = 286  # stop polling
    return resp


@require_http_methods(["POST"])
def email_login(request: HttpRequest):
    sentry_count("login.attempt", attributes={"type": "email"})
    form = EmailLoginForm(request.POST)
    if not form.is_valid():
        return render_error(request, _("Invalid captcha"))
    login_email = form.cleaned_data["email"]
    try:
        EmailValidator()(login_email)
    except Exception:
        return render_error(request, _("Invalid email address"))
    Email.send_login_email(request, login_email, "login")
    return render(
        request,
        "users/verify.html",
        {
            "msg": _("Verification"),
            "secondary_msg": _(
                "Verification email is being sent, please check your inbox."
            ),
            "action": "login",
            "email": login_email,
        },
    )


@require_http_methods(["GET", "POST"])
def email_verify(request: HttpRequest):
    if request.method == "GET":
        return render(request, "users/verify.html")
    fail_key = f"email_verify_fails_{client_ip(request)}"
    if (cache.get(fail_key) or 0) >= _MAX_VERIFY_FAILS:
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Too many attempts, please try again later."),
            },
        )
    code = request.POST.get("code", "").strip()
    account = Email.authenticate(request, code) if code else None
    if not account:
        try:
            cache.incr(fail_key)
        except ValueError:
            cache.set(fail_key, 1, timeout=_VERIFY_FAIL_TTL)
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    return process_verified_account(request, account)
