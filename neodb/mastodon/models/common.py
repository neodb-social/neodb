from datetime import timedelta

from django.core.cache import cache
from django.db import models
from django.db.models.functions import Lower, Upper
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from typedmodels.models import TypedModel

from common.sentry import count as sentry_count

# Domain-level circuit breaker — covers webfinger / check_alive failures
# (i.e. the instance itself is unreachable).
_DOMAIN_FAILURE_WINDOW = 3600  # 1h sliding window for failure counts
_DOMAIN_FAILURE_THRESHOLD = 5  # failures in the window before tripping
_DOMAIN_OPEN_COOLDOWN = 1800  # how long the breaker stays open

# Per-account circuit breaker — covers refresh failures (revoked tokens,
# deleted accounts) that are specific to one (uid, domain) pair on a server
# that is otherwise reachable.
_ACCOUNT_FAILURE_WINDOW = 7 * 86400  # 7d sliding window
_ACCOUNT_FAILURE_THRESHOLD = 3  # failures in the window before tripping
_ACCOUNT_OPEN_COOLDOWN = 86400  # 24h cooldown once tripped


class Platform(models.TextChoices):
    EMAIL = "email", _("Email")
    MASTODON = "mastodon", _("Mastodon")
    THREADS = "threads", _("Threads")
    BLUESKY = "bluesky", _("Bluesky")


class SocialAccount(TypedModel):
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="social_accounts",
        null=True,
    )
    domain = models.CharField(max_length=255, null=False, blank=False)
    # unique permanent id per domain per platform
    uid = models.CharField(max_length=255, null=False, blank=False)
    handle = models.CharField(max_length=1000, null=False, blank=False)

    access_data = models.JSONField(default=dict, null=False)
    account_data = models.JSONField(default=dict, null=False)
    preference_data = models.JSONField(default=dict, null=False)

    followers = models.JSONField(default=list)
    following = models.JSONField(default=list)
    mutes = models.JSONField(default=list)
    blocks = models.JSONField(default=list)
    domain_blocks = models.JSONField(default=list)

    created = models.DateTimeField(default=timezone.now)
    modified = models.DateTimeField(auto_now=True)
    last_refresh = models.DateTimeField(default=None, null=True)
    last_reachable = models.DateTimeField(default=None, null=True)

    # sync_profile = jsondata.BooleanField(
    #     json_field_name="preference_data", default=True
    # )
    # sync_graph = jsondata.BooleanField(json_field_name="preference_data", default=True)
    # sync_timeline = jsondata.BooleanField(
    #     json_field_name="preference_data", default=True
    # )

    class Meta:
        indexes = [
            models.Index(fields=["type", "handle"], name="index_social_type_handle"),
            models.Index(
                fields=["type", "domain", "uid"], name="index_social_type_domain_uid"
            ),
            # Backs handle__iexact lookups (UPPER, so the Lower unique index
            # below cannot serve them), e.g. APIdentity.get_by_linked_handle().
            models.Index("type", Upper("handle"), name="ix_social_type_handle_ci"),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("domain"), Lower("uid"), name="unique_social_domain_uid"
            ),
            models.UniqueConstraint(
                "type", Lower("handle"), name="unique_social_type_handle"
            ),
        ]

    def __str__(self) -> str:
        return f"({self.pk}){self.platform}@{self.handle}"

    @property
    def platform(self) -> Platform:
        return Platform(
            str(self.type).replace("mastodon.", "", 1).replace("account", "", 1)
        )

    def to_dict(self):
        # skip cached_property, datetime and other non-serializable fields
        d = {
            k: v
            for k, v in self.__dict__.items()
            if k
            not in [
                "created",
                "modified",
                "last_refresh",
                "last_reachable",
            ]
            and not k.startswith("_")
        }
        return d

    @classmethod
    def from_dict(cls, d: dict | None):
        return cls(**d) if d else None

    def check_alive(self) -> bool:
        return False

    def refresh(self) -> bool:
        return False

    def refresh_graph(self, save=True) -> bool:
        return False

    def _sync_metric_attrs(self, result: str) -> dict:
        return {
            "platform": self.platform.value,
            "domain": self.domain,
            "result": result,
        }

    def _emit_sync_result(self, result: str) -> None:
        sentry_count(
            "mastodon.usersync.account", attributes=self._sync_metric_attrs(result)
        )

    def _domain_circuit_key(self) -> str:
        return f"sync_open:{self.platform.value}:{self.domain}"

    def _domain_fail_key(self) -> str:
        return f"sync_fail:{self.platform.value}:{self.domain}"

    def _account_circuit_key(self) -> str:
        return f"sync_open_acct:{self.pk}"

    def _account_fail_key(self) -> str:
        return f"sync_fail_acct:{self.pk}"

    def _domain_circuit_open(self) -> bool:
        return bool(cache.get(self._domain_circuit_key()))

    def _account_circuit_open(self) -> bool:
        return bool(cache.get(self._account_circuit_key()))

    @staticmethod
    def _bump_failure_counter(key: str, window: int) -> int:
        # `cache.add` initialises the key (and TTL) atomically only when missing;
        # `cache.incr` then bumps the counter without resetting the TTL, so the
        # window stays fixed instead of sliding with every failure.
        if cache.add(key, 1, timeout=window):
            return 1
        try:
            return int(cache.incr(key))
        except ValueError:
            cache.set(key, 1, timeout=window)
            return 1

    def _record_domain_failure(self) -> None:
        fails = self._bump_failure_counter(
            self._domain_fail_key(), _DOMAIN_FAILURE_WINDOW
        )
        if fails >= _DOMAIN_FAILURE_THRESHOLD:
            cache.set(self._domain_circuit_key(), 1, timeout=_DOMAIN_OPEN_COOLDOWN)

    def _record_domain_success(self) -> None:
        cache.delete_many([self._domain_fail_key(), self._domain_circuit_key()])

    def _record_account_failure(self) -> None:
        fails = self._bump_failure_counter(
            self._account_fail_key(), _ACCOUNT_FAILURE_WINDOW
        )
        if fails >= _ACCOUNT_FAILURE_THRESHOLD:
            cache.set(self._account_circuit_key(), 1, timeout=_ACCOUNT_OPEN_COOLDOWN)

    def _record_account_success(self) -> None:
        cache.delete_many([self._account_fail_key(), self._account_circuit_key()])

    def sync(self, skip_graph=False, sleep_hours=0) -> bool:
        if self.last_refresh and self.last_refresh > timezone.now() - timedelta(
            hours=sleep_hours
        ):
            logger.debug(f"{self} skip refreshing as it's done recently")
            self._emit_sync_result("skip_ttl")
            return False
        if self._domain_circuit_open():
            logger.debug(
                f"{self} skip refreshing, domain circuit open for {self.domain}"
            )
            self._emit_sync_result("skip_circuit_domain")
            return False
        if self._account_circuit_open():
            logger.debug(f"{self} skip refreshing, account circuit open")
            self._emit_sync_result("skip_circuit_account")
            return False
        if not self.check_alive():
            d = (
                (timezone.now() - self.last_reachable).days
                if self.last_reachable
                else "unknown"
            )
            logger.warning(f"{self} unreachable for {d} days")
            self._record_domain_failure()
            self._emit_sync_result("fail_alive")
            return False
        # check_alive succeeded: the domain is reachable regardless of whether
        # this account's refresh ends up succeeding, so clear domain-level fail
        # state independently to avoid blocking healthy accounts on the domain.
        self._record_domain_success()
        if not self.refresh():
            logger.warning(f"{self} refresh failed")
            self._record_account_failure()
            self._emit_sync_result("fail_refresh")
            return False
        if not skip_graph:
            self.refresh_graph()
        logger.debug(f"{self} refreshed")
        self._record_account_success()
        self._emit_sync_result("ok")
        return True

    def sync_graph(self) -> int:
        return 0

    def on_disconnect(self) -> None:
        """platform-specific cleanup when the account is unlinked from a user"""
        pass
