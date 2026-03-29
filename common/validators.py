import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from loguru import logger
from validators import url as _url_validate


def is_valid_url(url: str | None) -> bool:
    """Validate that a URL is well-formed, uses HTTP(S), and does not resolve
    to a private/reserved IP address (防 DNS rebinding / SSRF)."""
    if not url:
        return False
    if not _url_validate(
        url,
        skip_ipv6_addr=True,
        skip_ipv4_addr=True,
        may_have_port=False,
        strict_query=False,
    ):
        return False
    hostname = urlparse(url).hostname
    if not hostname:
        return False
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.timeout, OSError):
        return False
    if not results:
        return False
    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            logger.warning(f"Blocked request to {hostname}: resolves to {ip}")
            return False
    return True


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
