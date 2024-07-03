from django.core.validators import EmailValidator
from django.http import HttpRequest
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.views import render_error

from ..models import Email
from .common import process_verified_account


@require_http_methods(["POST"])
def email_login(request: HttpRequest):
    login_email = request.POST.get("email", "")
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
        },
    )


@require_http_methods(["GET", "POST"])
def email_verify(request: HttpRequest):
    if request.method == "GET":
        return render(request, "users/verify.html")
    code = request.POST.get("code", "").strip()
    if not code:
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    account = Email.authenticate(request, code)
    if not account:
        return render(
            request,
            "users/verify.html",
            {
                "error": _("Invalid verification code"),
            },
        )
    return process_verified_account(request, account)
