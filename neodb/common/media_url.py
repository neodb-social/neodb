"""``MEDIA_URL`` decisions shared by settings and the media storage backend.

This module is imported while ``settings.py`` is still executing, so it must
stay free of Django imports: the settings object is half built at that point
and the app registry is not ready.
"""

from urllib.parse import urlparse


def _host_and_path(media_url: str) -> tuple[str, str]:
    parsed = urlparse(media_url)
    return (parsed.hostname or "").lower(), parsed.path.rstrip("/")


def s3_custom_domain(media_url: str, site_domains: list[str]) -> str:
    """The django-storages custom domain for ``MEDIA_URL``.

    One of our own domains, or no host at all, yields a path such as ``/m``,
    so a page on an alias domain loads media from that same domain and every
    alias has to route the path to the same storage. Any other host is a media
    host of its own, and keeps the absolute url django-storages builds from it.

    The comparison ignores the port, and a path keeps its leading slash, which
    is how ``S3Storage.url`` recognises one.
    """
    host, path = _host_and_path(media_url)
    if host and host not in site_domains:
        return host + path
    return path if path.startswith("/") else "/" + path


def media_url_at_site_root(media_url: str, site_domains: list[str]) -> bool:
    """``MEDIA_URL`` serves media from the root of one of our own domains.

    Every media key would then collide with the application's own paths.
    Reported by the ``neodb.E005`` check rather than raised here, so that
    ``neodb-manage check`` runs far enough to report it.
    """
    host, path = _host_and_path(media_url)
    return not path and (not host or host in site_domains)
