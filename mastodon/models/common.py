from datetime import timedelta

from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from typedmodels.models import TypedModel


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

    def sync(self, skip_graph=False, sleep_hours=0) -> bool:
        if self.last_refresh and self.last_refresh > timezone.now() - timedelta(
            hours=sleep_hours
        ):
            logger.debug(f"{self} skip refreshing as it's done recently")
            return False
        if not self.check_alive():
            d = (
                (timezone.now() - self.last_reachable).days
                if self.last_reachable
                else "unknown"
            )
            logger.warning(f"{self} unreachable for {d} days")
            return False
        if not self.refresh():
            logger.warning(f"{self} refresh failed")
            return False
        if not skip_graph:
            self.refresh_graph()
        logger.debug(f"{self} refreshed")
        return True

    def sync_graph(self) -> int:
        return 0
