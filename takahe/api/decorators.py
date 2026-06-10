from collections.abc import Callable
from functools import wraps

from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware

# Singleton used for manual CSRF verification on session-authenticated
# API requests.  Only process_view() is called; get_response is unused.
_csrf_middleware = CsrfViewMiddleware(lambda request: None)


def _csrf_check_failed(request) -> JsonResponse | None:
    """
    Verify the CSRF token for a session-authenticated API request.

    Returns a JsonResponse(403) on failure, or None on success.
    Safe HTTP methods (GET, HEAD, OPTIONS, TRACE) always pass.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None

    # Use a plain function as callback -- it must NOT carry csrf_exempt
    # so that CsrfViewMiddleware actually performs the check.
    def _callback(*a, **kw):
        pass

    result = _csrf_middleware.process_view(request, _callback, (), {})
    if result is not None:
        # process_view returns an HttpResponseForbidden on failure
        return JsonResponse({"error": "csrf_check_failed"}, status=403)
    return None


def identity_required(function):
    """
    Makes sure the token is tied to an identity, not an app only.
    """

    @wraps(function)
    def inner(request, *args, **kwargs):
        # They need an identity
        if not request.identity:
            return JsonResponse({"error": "identity_token_required"}, status=401)
        # Session-based auth (no Bearer token) requires CSRF verification
        if not request.token:
            csrf_resp = _csrf_check_failed(request)
            if csrf_resp:
                return csrf_resp
        return function(request, *args, **kwargs)

    # External API clients authenticate via Bearer tokens, which are not
    # vulnerable to CSRF.  The csrf_exempt flag prevents Django's
    # CsrfViewMiddleware from rejecting token-authenticated requests that
    # (correctly) omit a CSRF cookie.  When the request falls back to
    # session auth, _csrf_check_failed() above enforces CSRF manually.
    inner.csrf_exempt = True

    return inner


def scope_required(scope: str, requires_identity=True):
    """
    Asserts that the token we're using has the provided scope
    """

    def decorator(function: Callable):
        @wraps(function)
        def inner(request, *args, **kwargs):
            if not request.token:
                if request.identity:
                    # Session-based auth: enforce CSRF before granting access
                    csrf_resp = _csrf_check_failed(request)
                    if csrf_resp:
                        return csrf_resp
                else:
                    if request.config.public_timeline and scope == "read:statuses":
                        return function(request, *args, **kwargs)

                    return JsonResponse(
                        {"error": "identity_token_required"}, status=401
                    )
            elif not request.token.has_scope(scope):
                return JsonResponse({"error": "out_of_scope_for_token"}, status=403)
            # They need an identity
            if not request.identity and requires_identity:
                return JsonResponse({"error": "identity_token_required"}, status=401)
            return function(request, *args, **kwargs)

        # See identity_required for rationale on csrf_exempt.
        inner.csrf_exempt = True  # type:ignore
        return inner

    return decorator
