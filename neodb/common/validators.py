import ipaddress
import logging
import socket
from urllib.parse import urlparse

from cachetools import TTLCache
from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from validators import url as _url_validate

logger = logging.getLogger(__name__)

# In-process TTL cache for hostname → public-IP resolution. Avoids paying the
# DNS roundtrip twice when validating then fetching the same remote, and stays
# in-process to keep `socket.getaddrinfo` the only network dependency (so tests
# that patch it don't accidentally route a shared cache backend through the
# patched resolver).
_HOST_TTL = 300
_host_cache: TTLCache = TTLCache(maxsize=4096, ttl=_HOST_TTL)


def _lookup_public_ip(hostname: str) -> str | None:
    results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    if not results:
        raise socket.gaierror("No addresses")
    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_reserved or ip.is_multicast:
            logger.warning(f"Blocked request to {hostname}: resolves to {ip}")
            return None
    return str(ipaddress.ip_address(results[0][4][0]))


def resolve_public_ip(hostname: str) -> str | None:
    """Return a fresh address for a connection only if every DNS answer is public."""
    try:
        return _lookup_public_ip(hostname)
    except OSError:
        return None


def _resolve_hostname(hostname: str) -> bool | None:
    """True for public, False for blocked and None for unresolved."""
    cached = _host_cache.get(hostname)
    if cached is not None:
        return cached
    try:
        public = _lookup_public_ip(hostname) is not None
    except OSError:
        return None
    _host_cache[hostname] = public
    return public


def _hostname_is_public(hostname: str) -> bool:
    """Return True if `hostname` resolves only to public IPs."""
    return _resolve_hostname(hostname) is True


def _url_host_and_scheme(
    url: str | None, may_have_port: bool = False
) -> tuple[str, str] | None:
    """(hostname, scheme) of a well-formed URL, or None. Resolver not consulted."""
    if not url:
        return None
    if not _url_validate(
        url,
        skip_ipv6_addr=True,
        skip_ipv4_addr=True,
        may_have_port=may_have_port,
        strict_query=False,
    ):
        return None
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return None
    return hostname, parsed.scheme


def resolve_public_http_url(url: str, may_have_port: bool = False) -> str | None:
    parts = _url_host_and_scheme(url, may_have_port)
    if not parts or parts[1] not in ("http", "https"):
        return None
    return resolve_public_ip(parts[0])


def is_valid_url(url: str | None, may_have_port: bool = False) -> bool:
    """Validate that a URL is well-formed, uses HTTP(S), and does not resolve
    to a private/reserved IP address (DNS rebinding / SSRF)."""
    parts = _url_host_and_scheme(url, may_have_port)
    if not parts:
        return False
    return _hostname_is_public(parts[0])


def is_storable_url(url: str | None) -> bool:
    """Like `is_valid_url`, but a host the resolver cannot find is admitted.

    For URLs recorded rather than fetched -- the origin URL of an item
    rebuilt from a backup, whose server may no longer exist. A dead hostname
    is unreachable, not internal; private IPs are still rejected, and http(s)
    is required here though `is_valid_url` also accepts ftp.

    Admission is not authorization to fetch: whatever later dereferences the
    URL must gate on `is_valid_url` itself.
    """
    parts = _url_host_and_scheme(url)
    if not parts:
        return False
    hostname, scheme = parts
    if scheme not in ("http", "https"):
        return False
    return _resolve_hostname(hostname) is not False


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
    """Return the URL if safe, otherwise return the default.

    The three helpers below each call the Django check inline and return the
    checked value straight from its guarded branch, rather than sharing one
    implementation. That shape is what marks the result as sanitized for taint
    analysis; delegating to a wrapper, or negating the check and reassigning
    the variable, leaves callers reported as open redirects.
    """
    if not url:
        return default
    if not url_has_allowed_host_and_scheme(
        url,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        return default
    return url


def sanitize_next_url(url: str | None) -> str | None:
    """Return the URL if safe for redirect, otherwise return None."""
    if not url:
        return None
    if not url_has_allowed_host_and_scheme(
        url,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        return None
    return url


def get_safe_referer_url(request: HttpRequest, default: str = "/") -> str:
    """Get HTTP_REFERER if it's safe, otherwise return default."""
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        return default
    return referer
