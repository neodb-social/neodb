import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.utils.http import http_date
from loguru import logger

from .models import TakaheSession
from .utils import Takahe

_TAKAHE_SESSION_COOKIE_NAME = "sessionid"


@login_required
def auth_login(request):
    """Redirect to the login page if not yet, otherwise sync login info to takahe session"""
    Takahe.sync_password(request.user)
    # if SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies" in Takahe
    session = SessionStore(session_key=request.COOKIES.get(_TAKAHE_SESSION_COOKIE_NAME))
    session._session_cache = request.session._session  # type: ignore
    session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    session_key: str = session._get_session_key()  # type: ignore

    # if SESSION_ENGINE = "django.contrib.sessions.backends.db"
    # sess = request.session._session
    # sess["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    # logger.info(f"session: {sess}")
    # TakaheSession.objects.update_or_create(
    #     session_key=request.session.session_key,
    #     defaults={
    #         "session_data": request.session.encode(sess),
    #         "expire_date": request.session.get_expiry_date(),
    #     },
    # )
    # session_key = request.session.session_key

    response = redirect(request.GET.get("next", "/"))
    if request.session.get_expire_at_browser_close():
        max_age = None
        expires = None
    else:
        max_age = request.session.get_expiry_age()
        expires_time = time.time() + max_age
        expires = http_date(expires_time)
    response.set_cookie(
        _TAKAHE_SESSION_COOKIE_NAME,
        session_key,
        max_age=max_age,
        expires=expires,
        domain=settings.SESSION_COOKIE_DOMAIN,
        path=settings.SESSION_COOKIE_PATH,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    return response


def auth_logout(request: HttpRequest):
    response = redirect("/account/logout")
    response.delete_cookie(_TAKAHE_SESSION_COOKIE_NAME)
    return response
