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
            request.session = DummySession()  # type: ignore
            return
        super().process_request(request)

    def process_response(self, request, response):
        if isinstance(request.session, DummySession):
            return response
        return super().process_response(request, response)


class IdentityMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.identity = None
        if hasattr(request, "user") and request.user.is_authenticated:
            from users.models import APIdentity

            try:
                request.identity = APIdentity.objects.get(user=request.user)
            except APIdentity.DoesNotExist:
                pass
