from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def is_safe_url(url: str | None, allowed_hosts: set[str] | None = None) -> bool:
    """Check if a URL is safe for redirect (same-site or allowed hosts only)."""
    if not url:
        return False
    if allowed_hosts is None:
        allowed_hosts = set(settings.SITE_DOMAINS)
    return url_has_allowed_host_and_scheme(
        url=url, allowed_hosts=allowed_hosts, require_https=settings.SSL_ONLY
    )


def get_safe_redirect_url(url: str | None, default: str = "/") -> str:
    """Return the URL if safe, otherwise return the default."""
    if url and is_safe_url(url):
        return url
    return default


def sanitize_next_url(url: str | None) -> str | None:
    """Return the URL if safe for redirect, otherwise return None."""
    return url if is_safe_url(url) else None


def get_safe_referer_url(request: HttpRequest, default: str = "/") -> str:
    """Get HTTP_REFERER if it's safe, otherwise return default."""
    return get_safe_redirect_url(request.META.get("HTTP_REFERER", ""), default)
