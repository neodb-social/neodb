import time

from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.deprecation import MiddlewareMixin


class DummySession(dict):
    """A session-like object that never persists, used for API requests."""

    modified = False
    accessed = False

    def flush(self):
        self.clear()

    def cycle_key(self):
        pass

    def set_expiry(self, _value):
        pass

    def is_empty(self):
        return True


class APIAwareSessionMiddleware(SessionMiddleware):
    """
    SessionMiddleware that skips session persistence for API requests.
    API requests get a DummySession that behaves like an empty dict but never saves.
    """

    def process_request(self, request):
        if request.path.startswith("/api/"):
            request.session = DummySession()
            return
        super().process_request(request)

    def process_response(self, request, response):
        if isinstance(request.session, DummySession):
            return response
        return super().process_response(request, response)


class SiteConfigMiddleware:
    """
    Periodically refreshes SiteConfig from the database and writes
    values back to django.conf.settings for backward compatibility.
    """

    refresh_interval: float = 30.0

    def __init__(self, get_response):
        self.get_response = get_response
        self.config_ts: float = 0.0

    def __call__(self, request):
        from common.models import SiteConfig

        if not getattr(SiteConfig, "__forced__", False):
            now = time.monotonic()
            if (
                not getattr(SiteConfig, "system", None)
                or (now - self.config_ts) >= self.refresh_interval
            ):
                SiteConfig.system = SiteConfig.load_system()
                self.config_ts = now
                SiteConfig._apply_to_settings(SiteConfig.system)
        return self.get_response(request)


class IdentityMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.identity = None
        if hasattr(request, "user") and request.user.is_authenticated:
            from users.models import APIdentity

            try:
                request.identity = APIdentity.objects.get(user=request.user)
            except APIdentity.DoesNotExist:
                pass
