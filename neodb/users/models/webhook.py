import ipaddress
import logging
import socket
from urllib.parse import urlsplit, urlunsplit

import django_rq
import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone

from common.models import SiteConfig
from common.validators import is_valid_url
from takahe.models import Token

from .user import User

logger = logging.getLogger(__name__)

_ENABLED_CACHE_KEY = "webhook_on:{}"
_FAIL_CACHE_KEY = "webhook_fail:{}"
_FAIL_LIMIT = 100
_FAIL_WINDOW = 7 * 24 * 3600
_URL_MAX_LENGTH = 1000
MAX_WEBHOOKS_PER_USER = 5
WEBHOOK_PAYLOAD_VERSION = 1


class WebhookLimitReached(Exception):
    """The user already has MAX_WEBHOOKS_PER_USER webhooks for other apps."""


def scope_set(scopes: object) -> set[str]:
    """Token scopes are a list or a space separated string depending on the
    issuer; normalise so membership checks are exact either way."""
    if isinstance(scopes, str):
        return set(scopes.split())
    if isinstance(scopes, list | tuple | set):
        return {str(s) for s in scopes}
    return set()


class Webhook(models.Model):
    """One URL per (user, application) receiving fire-and-forget POSTs on
    the user's journal changes. `application_id` is a takahe Application pk,
    kept as a plain integer because takahe lives in another database."""

    user = models.ForeignKey(User, models.CASCADE, related_name="webhooks")
    application_id = models.IntegerField(db_index=True)
    url = models.URLField(max_length=_URL_MAX_LENGTH)
    disabled = models.BooleanField(default=False)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "application_id"], name="unique_user_app_webhook"
            )
        ]

    def __str__(self):
        return f"Webhook:{self.pk}:{self.application_id}:{self.url}"


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


def set_webhook(user: User, application_id: int, url: str) -> Webhook:
    """Create or replace the webhook of an application for a user; saving
    re-enables one that was disabled after repeated failures. Adding one
    for a new application raises WebhookLimitReached beyond the per-user cap;
    replacing an existing one always works."""
    with transaction.atomic():
        # lock the user row so concurrent adds for different apps serialise
        # on the count; the unique constraint alone only covers one app
        list(User.objects.select_for_update().filter(pk=user.pk).only("id"))
        webhooks = Webhook.objects.filter(user_id=user.pk)
        if (
            not webhooks.filter(application_id=application_id).exists()
            and webhooks.count() >= MAX_WEBHOOKS_PER_USER
        ):
            raise WebhookLimitReached()
        webhook, created = Webhook.objects.update_or_create(
            user=user,
            application_id=application_id,
            defaults={"url": url, "disabled": False},
        )
    clear_webhook_failures(webhook.pk)
    clear_webhook_cache(user.pk)
    return webhook


def remove_webhook(user_id: int, application_id: int | None = None) -> None:
    """Delete the user's webhook of one application, or of all when the
    application is not given (all tokens revoked)."""
    webhooks = Webhook.objects.filter(user_id=user_id)
    if application_id is not None:
        webhooks = webhooks.filter(application_id=application_id)
    for pk in webhooks.values_list("pk", flat=True):
        clear_webhook_failures(pk)
    webhooks.delete()
    clear_webhook_cache(user_id)


def build_webhook_payload(username: str, changes: list[dict]) -> dict:
    """The delivery envelope: `changes` is a list so several objects can
    share one delivery later; each entry is {"type", "action", "object"}."""
    return {
        "version": WEBHOOK_PAYLOAD_VERSION,
        "site": settings.SITE_INFO["site_url"],
        "time": timezone.now().isoformat(timespec="seconds"),
        "username": username,
        "changes": changes,
    }


def dispatch_webhook(user_id: int, username: str, changes: list[dict]) -> None:
    """Queue a delivery if the user has any active webhook; called from
    journal piece save/delete hooks, so it must stay cheap. Enqueued on
    commit so a rolled-back change never notifies."""
    if not has_active_webhook(user_id):
        return
    payload = build_webhook_payload(username, changes)
    transaction.on_commit(
        lambda: django_rq.get_queue("webhook").enqueue(
            _deliver_webhook, user_id, payload
        )
    )


def ping_webhooks(user: User) -> int:
    """Queue one delivery with an empty `changes` list to every active
    webhook of the user, so receivers can be tested; returns how many."""
    count = Webhook.objects.filter(user=user, disabled=False).count()
    if count:
        identity = getattr(user, "identity", None)
        dispatch_webhook(user.pk, identity.handle if identity else user.username, [])
    return count


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
        # is_global also rejects shared address space (100.64.0.0/10) and
        # multicast, which the private/reserved/loopback predicates admit
        if not addr.is_global or addr.is_multicast:
            return None
    return ips[0] if ips else None


def _post_webhook(url: str, payload: dict, timeout: float) -> bool:
    """POST without trusting DNS twice or the response: pin the address
    that passed the public-IP check (DNS rebinding) and close the
    response without reading its body."""
    if not url or len(url) > _URL_MAX_LENGTH:
        return False
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    headers = {"User-Agent": settings.NEODB_USER_AGENT}
    if settings.DEBUG and parts.scheme in ("http", "https"):
        with (
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, json=payload, headers=headers) as resp,
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
            headers={**headers, "Host": host_header},
            extensions={"sni_hostname": hostname},
        ) as resp,
    ):
        return resp.is_success


def has_live_token(identity_id: int | None, application_id: int) -> bool:
    """Whether the application still holds an unrevoked token with the read
    scope for the identity: payloads disclose what changed, so a surviving
    write-only token is not enough."""
    if identity_id is None:
        return False
    scopes = Token.objects.filter(
        identity_id=identity_id, application_id=application_id, revoked__isnull=True
    ).values_list("scopes", flat=True)
    return any("read" in scope_set(s) for s in scopes)


def _deliver_webhook(user_id: int, payload: dict) -> None:
    """rq job: POST payload to each active webhook of the user, fire and
    forget: no retry, failures only logged. A webhook whose application no
    longer holds a read token for the user is dropped instead of called
    (disabled ones too, so they stop taking a slot), so revoking an app
    anywhere also stops its webhook."""
    user = User.objects.filter(pk=user_id).select_related("identity").first()
    if not user:
        return
    identity = getattr(user, "identity", None)
    identity_id = identity.pk if identity else None
    timeout = (SiteConfig.system.webhook_timeout or 1000) / 1000
    for webhook in Webhook.objects.filter(user_id=user_id).order_by("pk"):
        if not has_live_token(identity_id, webhook.application_id):
            logger.info(f"webhook {webhook.pk} dropped: application has no token")
            remove_webhook(user_id, webhook.application_id)
            continue
        if webhook.disabled:
            continue
        try:
            ok = _post_webhook(webhook.url, payload, timeout)
        except Exception as e:
            ok = False
            logger.warning(f"webhook {webhook.pk} delivery failed: {e}")
        if ok:
            clear_webhook_failures(webhook.pk)
        elif _bump_failures(webhook.pk) >= _FAIL_LIMIT:
            # url filter: a webhook replaced meanwhile keeps its fresh start
            Webhook.objects.filter(pk=webhook.pk, url=webhook.url).update(disabled=True)
            clear_webhook_cache(user_id)
            clear_webhook_failures(webhook.pk)
            logger.warning(
                f"webhook {webhook.pk} disabled after {_FAIL_LIMIT} failures"
            )
