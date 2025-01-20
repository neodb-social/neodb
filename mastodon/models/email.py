import random

import django_rq
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.signing import b62_encode
from django.http import HttpRequest
from django.utils.translation import gettext as _
from loguru import logger

from .common import SocialAccount

_code_ttl = 60 * 15


class EmailAccount(SocialAccount):
    url = None

    def sync(self, skip_graph=False, sleep_hours=0) -> bool:
        return True


class Email:
    @staticmethod
    def new_account(email: str) -> EmailAccount | None:
        sp = email.split("@", 1)
        if len(sp) != 2:
            return None
        account = EmailAccount(handle=email, uid=sp[0], domain=sp[1])
        return account

    @staticmethod
    def _send(email, subject, body):
        try:
            logger.debug(f"Sending email to {email} with subject {subject}")
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"send email to {email} failed: {e}")

    @staticmethod
    def generate_login_email(email: str, action: str) -> tuple[str, str]:
        if action != "verify":
            account = EmailAccount.objects.filter(handle__iexact=email).first()
            action = "login" if account and account.user else "register"
        s = {"e": email, "a": action}
        # v = TimestampSigner().sign_object(s)
        code = b62_encode(random.randint(pow(62, 4), pow(62, 5) - 1))
        cache.set(f"login_{code}", s, timeout=_code_ttl)
        footer = _(
            "\n\nIf you did not mean to register or login, please ignore this email. If you are concerned with your account security, please change the email linked with your account, or contact us."
        )
        site = settings.SITE_INFO["site_name"]
        match action:
            case "verify":
                subject = f"{site} - {_('Verification Code')} - {code}"
                msg = _(
                    "Use this code to verify your email address {email}\n\n{code}"
                ).format(email=email, code=code)
            case "login":
                subject = f"{site} - {_('Verification Code')} - {code}"
                msg = _("Use this code to login as {email}\n\n{code}").format(
                    email=email, code=code
                )
            case "register":
                subject = f"{site} - {_('Register')}"
                msg = _(
                    "There is no account registered with this email address yet: {email}\n\nIf you already have an account with us, just login and add this email to you account.\n\nIf you prefer to register a new account with this email, please use this verification code: {code}"
                ).format(email=email, code=code)
        return subject, msg + footer

    @staticmethod
    def send_login_email(request: HttpRequest, email: str, action: str):
        request.session["pending_email"] = email
        subject, body = Email.generate_login_email(email, action)
        django_rq.get_queue("mastodon").enqueue(Email._send, email, subject, body)

    @staticmethod
    def authenticate(request: HttpRequest, code: str) -> EmailAccount | None:
        if not request.session.get("pending_email"):
            return None
        s: dict = cache.get(f"login_{code}")
        email = (s or {}).get("e")
        if not email or request.session.get("pending_email") != email:
            return None
        cache.delete(f"login_{code}")
        del request.session["pending_email"]
        existing_account = EmailAccount.objects.filter(handle__iexact=email).first()
        if existing_account:
            return existing_account
        return Email.new_account(email)
