import json
import time

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed

from core import sentry
from core.models import Config, ConfigResolver


class HeadersMiddleware:
    """
    Deals with Accept request headers, and Cache-Control response ones.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        accept = request.headers.get("accept", "text/html").lower()
        request.ap_json = (
            "application/json" in accept
            or "application/ld" in accept
            or "application/activity" in accept
        )
        response = self.get_response(request)
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


class ConfigLoadingMiddleware:
    """
    Caches the system config every request
    """

    refresh_interval: float = 5.0

    def __init__(self, get_response):
        self.get_response = get_response
        self.config_ts: float = 0.0

    def __call__(self, request):
        # Allow test fixtures to force and lock the config
        if not getattr(Config, "__forced__", False):
            if (
                not getattr(Config, "system", None)
                or (time.monotonic() - self.config_ts) >= self.refresh_interval
            ):
                Config.system = Config.load_system()
                self.config_ts = time.monotonic()
        # Install a ConfigResolver on the request to automatically check domain configs.
        request.config = ConfigResolver(Config.system)
        if request.domain:
            request.config.add(request.domain.config_domain)
        if request.user.is_authenticated:
            request.config.add(request.user.config_user)
        if request.identity:
            request.config.add(request.identity.config_identity)
        return self.get_response(request)


class SentryTaggingMiddleware:
    """
    Sets Sentry tags at the start of the request if Sentry is configured.
    """

    def __init__(self, get_response):
        if not sentry.SENTRY_ENABLED:
            raise MiddlewareNotUsed()
        self.get_response = get_response

    def __call__(self, request):
        sentry.set_takahe_app("web")
        response = self.get_response(request)
        return response


def show_toolbar(request):
    """
    Determines whether to show the debug toolbar on a given page.
    """
    return settings.DEBUG and request.user.is_authenticated and request.user.admin


class ParamsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def make_params(self, request):
        # See https://docs.joinmastodon.org/client/intro/#parameters
        # If they sent JSON, use that.
        if request.content_type == "application/json" and request.body.strip():
            return json.loads(request.body)
        # Otherwise, fall back to form data.
        params = {}
        for key, value in request.GET.lists():
            params[key] = value[0] if len(value) == 1 else value
        for key, value in request.POST.lists():
            params[key] = value[0] if len(value) == 1 else value
        return params

    def __call__(self, request):
        request.PARAMS = self.make_params(request)
        return self.get_response(request)
