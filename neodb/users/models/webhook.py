import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

import django_rq
import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from loguru import logger

from common.models import SiteConfig
from common.validators import is_valid_url

from .user import User

_ENABLED_CACHE_KEY = "webhook_on:{}"
_FAIL_CACHE_KEY = "webhook_fail:{}"
_FAIL_LIMIT = 100
_FAIL_WINDOW = 7 * 24 * 3600
_URL_MAX_LENGTH = 1000


class Webhook(models.Model):
    """A user-configured URL receiving fire-and-forget POSTs on journal changes."""

    user = models.ForeignKey(User, models.CASCADE, related_name="webhooks")
    url = models.URLField(max_length=_URL_MAX_LENGTH)
    disabled = models.BooleanField(default=False)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "url"], name="unique_user_webhook_url"
            )
        ]

    def __str__(self):
        return f"Webhook:{self.pk}:{self.url}"


def validate_webhook_url(url: str) -> bool:
    """Accept only well-formed https URLs that resolve to public IPs;
    DEBUG relaxes both checks so local endpoints can be tested."""
    if not url or len(url) > _URL_MAX_LENGTH:
        return False
    if settings.DEBUG:
        return url.startswith(("http://", "https://"))
    if not url.startswith("https://"):
        return False
    return is_valid_url(url, may_have_port=True)


def has_active_webhook(user_id: int) -> bool:
    if SiteConfig.system.webhook_max_subscriptions <= 0:
        return False
    return bool(
        cache.get_or_set(
            _ENABLED_CACHE_KEY.format(user_id),
            lambda: Webhook.objects.filter(user_id=user_id, disabled=False).exists(),
            300,
        )
    )


def clear_webhook_cache(user_id: int) -> None:
    cache.delete(_ENABLED_CACHE_KEY.format(user_id))


def clear_webhook_failures(pk: int) -> None:
    cache.delete(_FAIL_CACHE_KEY.format(pk))


def dispatch_webhook(user_id: int, payload: dict[str, str]) -> None:
    """Queue a delivery if the user has any active webhook; called from
    journal piece save/delete hooks, so it must stay cheap. Enqueued on
    commit so a rolled-back change never notifies."""
    if not has_active_webhook(user_id):
        return
    transaction.on_commit(
        lambda: django_rq.get_queue("webhook").enqueue(
            _deliver_webhook, user_id, payload
        )
    )


def _bump_failures(pk: int) -> int:
    # `cache.add` initialises the key (and TTL) atomically only when missing;
    # `cache.incr` then bumps the counter without resetting the TTL.
    key = _FAIL_CACHE_KEY.format(pk)
    if cache.add(key, 1, timeout=_FAIL_WINDOW):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        cache.set(key, 1, timeout=_FAIL_WINDOW)
        return 1


def _resolve_public_ip(hostname: str) -> str | None:
    """Resolve fresh (no cache) and return one address only when every
    answer is public, so the POST connects to what was just checked."""
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    ips = [str(sockaddr[0]) for _, _, _, _, sockaddr in results]
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if (
            addr.is_private
            or addr.is_reserved
            or addr.is_loopback
            or addr.is_link_local
        ):
            return None
    return ips[0] if ips else None


def _post_webhook(url: str, payload: dict[str, str], timeout: float) -> bool:
    """POST without trusting DNS twice or the response: pin the address
    that passed the public-IP check (防 DNS rebinding) and close the
    response without reading its body."""
    if not url or len(url) > _URL_MAX_LENGTH:
        return False
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    if settings.DEBUG and parts.scheme in ("http", "https"):
        with (
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, json=payload) as resp,
        ):
            return resp.is_success
    if parts.scheme != "https" or not hostname:
        return False
    ip = _resolve_public_ip(hostname)
    if not ip:
        return False
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
    pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    host_header = f"{hostname}:{parts.port}" if parts.port else hostname
    with (
        httpx.Client(timeout=timeout, follow_redirects=False) as client,
        client.stream(
            "POST",
            pinned,
            json=payload,
            headers={"Host": host_header},
            extensions={"sni_hostname": hostname},
        ) as resp,
    ):
        return resp.is_success


def _deliver_webhook(user_id: int, payload: dict[str, str]) -> None:
    """rq job: POST payload to each active webhook of the user, fire and
    forget: no retry, failures only logged. Honors the instance limit at
    delivery time, so lowering it takes effect without a user re-save."""
    system = SiteConfig.system
    limit = system.webhook_max_subscriptions
    if limit <= 0:
        return
    timeout = (system.webhook_timeout or 1000) / 1000
    webhooks = Webhook.objects.filter(user_id=user_id, disabled=False).order_by("pk")
    for webhook in webhooks[:limit]:
        try:
            ok = _post_webhook(webhook.url, payload, timeout)
        except Exception as e:
            ok = False
            logger.warning(f"webhook {webhook.pk} delivery failed: {e}")
        if ok:
            clear_webhook_failures(webhook.pk)
        elif _bump_failures(webhook.pk) > _FAIL_LIMIT:
            Webhook.objects.filter(pk=webhook.pk).update(disabled=True)
            clear_webhook_cache(user_id)
            clear_webhook_failures(webhook.pk)
            logger.warning(
                f"webhook {webhook.pk} disabled after {_FAIL_LIMIT} failures"
            )
