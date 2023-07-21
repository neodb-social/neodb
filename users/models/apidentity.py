from functools import cached_property

from django.conf import settings
from django.db import models
from loguru import logger

from takahe.utils import Takahe

from .user import User


class APIdentity(models.Model):
    """
    An identity/actor in ActivityPub service.

    This model is used as 1:1 mapping to Takahe Identity Model
    """

    user = models.OneToOneField("User", models.CASCADE, related_name="identity")
    local = models.BooleanField()
    username = models.CharField(max_length=500, blank=True, null=True)
    domain_name = models.CharField(max_length=500, blank=True, null=True)
    deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["local", "username"]),
            models.Index(fields=["domain_name", "username"]),
        ]

    @cached_property
    def takahe_identity(self):
        return Takahe.get_identity(self.pk)

    @property
    def is_active(self):
        return self.user.is_active and self.takahe_identity.deleted is None

    @property
    def name(self):
        return self.takahe_identity.name

    @property
    def discoverable(self):
        return self.takahe_identity.discoverable

    @property
    def actor_uri(self):
        return self.takahe_identity.actor_uri

    @property
    def icon_uri(self):
        return self.takahe_identity.icon_uri

    @property
    def display_name(self):
        return self.takahe_identity.name

    @property
    def avatar(self):
        return self.user.avatar  # FiXME

    @property
    def url(self):
        return f"/users/{self.handler}/"

    @property
    def preference(self):
        return self.user.preference

    @property
    def handler(self):
        if self.local:
            return self.username
        else:
            return f"{self.username}@{self.domain_name}"

    @property
    def following(self):
        return Takahe.get_following_ids(self.pk)

    @property
    def muting(self):
        return Takahe.get_muting_ids(self.pk)

    @property
    def blocking(self):
        return Takahe.get_blocking_ids(self.pk)

    @property
    def rejecting(self):
        return Takahe.get_rejecting_ids(self.pk)

    @property
    def ignoring(self):
        return self.muting + self.rejecting

    def follow(self, target: "APIdentity"):
        Takahe.follow(self.pk, target.pk)

    def unfollow(self, target: "APIdentity"):  # this also cancels follow request
        Takahe.unfollow(self.pk, target.pk)

    def requested_followers(self):
        Takahe.get_requested_follower_ids(self.pk)

    def following_request(self):
        Takahe.get_following_request_ids(self.pk)

    def accept_follow_request(self, target: "APIdentity"):
        Takahe.accept_follow_request(self.pk, target.pk)

    def reject_follow_request(self, target: "APIdentity"):
        Takahe.reject_follow_request(self.pk, target.pk)

    def block(self, target: "APIdentity"):
        Takahe.block(self.pk, target.pk)

    def unblock(self, target: "APIdentity"):
        Takahe.unblock(self.pk, target.pk)

    def mute(self, target: "APIdentity"):
        Takahe.mute(self.pk, target.pk)

    def unmute(self, target: "APIdentity"):
        Takahe.unmute(self.pk, target.pk)

    def is_rejecting(self, target: "APIdentity"):
        return self != target and (
            target.is_blocked_by(self) or target.is_blocking(self)
        )

    def is_blocking(self, target: "APIdentity"):
        return target.pk in self.blocking

    def is_blocked_by(self, target: "APIdentity"):
        return target.is_blocking(self)

    def is_muting(self, target: "APIdentity"):
        return target.pk in self.muting

    def is_following(self, target: "APIdentity"):
        return target.pk in self.following

    def is_followed_by(self, target: "APIdentity"):
        return target.is_following(self)

    def is_visible_to_user(self, viewing_user: User):
        return (
            viewing_user.is_authenticated
            or viewing_user == self.user
            or (
                not self.is_blocking(viewing_user.identity)
                and not self.is_blocked_by(viewing_user.identity)
            )
        )

    @classmethod
    def get_by_handler(cls, handler: str) -> "APIdentity":
        s = handler.split("@")
        if len(s) == 1:
            return cls.objects.get(username=s[0], local=True, deleted__isnull=True)
        elif len(s) == 2:
            return cls.objects.get(
                user__mastodon_username=s[0],
                user__mastodon_site=s[1],
                deleted__isnull=True,
            )
        elif len(s) == 3 and s[0] == "":
            return cls.objects.get(
                username=s[0], domain_name=s[1], local=False, deleted__isnull=True
            )
        else:
            raise cls.DoesNotExist(f"Invalid handler {handler}")

    @cached_property
    def activity_manager(self):
        from social.models import ActivityManager

        return ActivityManager(self)

    @cached_property
    def shelf_manager(self):
        from journal.models import ShelfManager

        return ShelfManager(self)

    @cached_property
    def tag_manager(self):
        from journal.models import TagManager

        return TagManager(self)
