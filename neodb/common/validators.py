import ipaddress
import socket
from urllib.parse import urlparse

from cachetools import TTLCache
from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from loguru import logger
from validators import url as _url_validate

# In-process TTL cache for hostname → public-IP resolution. Avoids paying the
# DNS roundtrip twice when validating then fetching the same remote, and stays
# in-process to keep `socket.getaddrinfo` the only network dependency (so tests
# that patch it don't accidentally route a shared cache backend through the
# patched resolver).
_HOST_TTL = 300
_host_cache: TTLCache = TTLCache(maxsize=4096, ttl=_HOST_TTL)


def _resolve_hostname(hostname: str) -> bool | None:
    """Tri-state resolution of `hostname`.

    True when it resolves only to public IPs, False when any answer is
    private/reserved, and None when the resolver gives no answer at all.
    The last is what separates "points inside our network" from "does not
    exist any more", which callers weigh differently.
    """
    cached = _host_cache.get(hostname)
    if cached is not None:
        return cached
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror, socket.timeout, OSError:
        # Don't cache transient resolver failures so they recover quickly.
        return None
    if not results:
        return None
    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            logger.warning(f"Blocked request to {hostname}: resolves to {ip}")
            _host_cache[hostname] = False
            return False
    _host_cache[hostname] = True
    return True


def _hostname_is_public(hostname: str) -> bool:
    """Return True if `hostname` resolves only to public IPs."""
    return _resolve_hostname(hostname) is True


def _url_host_and_scheme(url: str | None) -> tuple[str, str] | None:
    """(hostname, scheme) of a well-formed URL, or None. Resolver not consulted."""
    if not url:
        return None
    if not _url_validate(
        url,
        skip_ipv6_addr=True,
        skip_ipv4_addr=True,
        may_have_port=False,
        strict_query=False,
    ):
        return None
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return None
    return hostname, parsed.scheme


def is_valid_url(url: str | None) -> bool:
    """Validate that a URL is well-formed, uses HTTP(S), and does not resolve
    to a private/reserved IP address (DNS rebinding / SSRF)."""
    parts = _url_host_and_scheme(url)
    if not parts:
        return False
    return _hostname_is_public(parts[0])


def is_storable_url(url: str | None) -> bool:
    """Like `is_valid_url`, but a host the resolver cannot find is admitted.

    For URLs that are recorded rather than fetched -- the origin URL of an
    item rebuilt from a backup, whose server may no longer exist. A dead
    hostname is unreachable, not internal, so rejecting it would throw away
    exactly the data this is for. Literal private IPs and hostnames that do
    resolve into private space are still rejected.

    Admission here is not authorization to fetch: anything that later
    dereferences the URL must gate on `is_valid_url` itself.

    The http(s) restriction is enforced here, unlike in `is_valid_url`, whose
    underlying validator also accepts ftp and whose callers predate this.
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
