from functools import cached_property

from django.conf import settings
from django.db import models
from django.templatetags.static import static

from takahe.utils import Takahe

from .preference import Preference
from .user import User


class APIdentity(models.Model):
    """
    An identity/actor in ActivityPub service.

    This model is used as 1:1 mapping to Takahe Identity Model
    """

    user = models.OneToOneField(
        "User", models.SET_NULL, related_name="identity", null=True
    )
    local = models.BooleanField()
    username = models.CharField(max_length=500, blank=True, null=True)
    domain_name = models.CharField(max_length=500, blank=True, null=True)
    deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["local", "username"]),
            models.Index(fields=["domain_name", "username"]),
        ]

    def __str__(self):
        return f"{self.pk}:{self.username}@{self.domain_name}"

    @cached_property
    def takahe_identity(self):
        return Takahe.get_identity(self.pk)

    @property
    def is_active(self):
        return (
            self.user and self.user.is_active and self.takahe_identity.deleted is None
        )

    @property
    def name(self):
        return self.takahe_identity.name

    @property
    def discoverable(self):
        return self.takahe_identity.discoverable

    @property
    def locked(self):
        return self.takahe_identity.manually_approves_followers

    @property
    def actor_uri(self):
        return self.takahe_identity.actor_uri

    @property
    def icon_uri(self):
        return self.takahe_identity.icon_uri

    @property
    def profile_uri(self):
        return self.takahe_identity.profile_uri

    @cached_property
    def display_name(self):
        return self.takahe_identity.name or self.username

    @cached_property
    def summary(self):
        return self.takahe_identity.summary or ""

    @property
    def avatar(self):
        if self.local:
            return (
                self.takahe_identity.icon.url
                if self.takahe_identity.icon
                else settings.SITE_INFO["user_icon"]
            )
        else:
            return f"/proxy/identity_icon/{self.pk}/"

    @property
    def url(self):
        return f"/users/{self.handler}/"

    @property
    def preference(self):
        return self.user.preference if self.user else Preference()

    @property
    def full_handle(self):
        return f"@{self.username}@{self.domain_name}"

    @property
    def handler(self):
        if self.local:
            return self.username
        else:
            return f"@{self.username}@{self.domain_name}"

    @property
    def following(self):
        return Takahe.get_following_ids(self.pk)

    @property
    def followers(self):
        return Takahe.get_follower_ids(self.pk)

    @property
    def muting(self):
        return Takahe.get_muting_ids(self.pk)

    @property
    def blocking(self):
        return Takahe.get_blocking_ids(self.pk)

    @property
    def following_identities(self):
        return APIdentity.objects.filter(pk__in=self.following)

    @property
    def follower_identities(self):
        return APIdentity.objects.filter(pk__in=self.followers)

    @property
    def muting_identities(self):
        return APIdentity.objects.filter(pk__in=self.muting)

    @property
    def blocking_identities(self):
        return APIdentity.objects.filter(pk__in=self.blocking)

    @property
    def requested_follower_identities(self):
        return APIdentity.objects.filter(pk__in=self.requested_followers)

    @property
    def follow_requesting_identities(self):
        return APIdentity.objects.filter(pk__in=self.following_request)

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

    @property
    def requested_followers(self):
        return Takahe.get_requested_follower_ids(self.pk)

    @property
    def following_request(self):
        return Takahe.get_following_request_ids(self.pk)

    def accept_follow_request(self, target: "APIdentity"):
        Takahe.accept_follow_request(target.pk, self.pk)

    def reject_follow_request(self, target: "APIdentity"):
        Takahe.reject_follow_request(target.pk, self.pk)

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

    def is_requesting(self, target: "APIdentity"):
        return target.pk in self.following_request

    def is_requested(self, target: "APIdentity"):
        return target.pk in self.requested_followers

    def is_followed_by(self, target: "APIdentity"):
        return target.is_following(self)

    def is_visible_to_user(self, viewing_user: User):
        return (
            (not viewing_user.is_authenticated)
            or viewing_user == self.user
            or (
                not self.is_blocking(viewing_user.identity)
                and not self.is_blocked_by(viewing_user.identity)
            )
        )

    @classmethod
    def get_by_handler(cls, handler: str) -> "APIdentity":
        """
        Handler format
        'id' - local identity with username 'id'
        'id@site' - local identity with linked mastodon id == 'id@site'
        '@id' - local identity with username 'id'
        '@id@site' - remote activitypub identity 'id@site'
        """
        s = handler.split("@")
        l = len(s)
        if l == 1 or (l == 2 and s[0] == ""):
            return cls.objects.get(
                username__iexact=s[0] if l == 1 else s[1],
                local=True,
                deleted__isnull=True,
            )
        elif l == 2:
            return cls.objects.get(
                user__mastodon_username__iexact=s[0],
                user__mastodon_site__iexact=s[1],
                deleted__isnull=True,
            )
        elif l == 3 and s[0] == "":
            i = cls.objects.filter(
                username__iexact=s[1], domain_name__iexact=s[2], deleted__isnull=True
            ).first()
            if i:
                return i
            if s[2].lower() != settings.SITE_INFO["site_domain"].lower():
                identity = Takahe.get_identity_by_handler(s[1], s[2])
                if identity:
                    return Takahe.get_or_create_remote_apidentity(identity)
            raise cls.DoesNotExist(f"Identity not exist {handler}")
        else:
            raise cls.DoesNotExist(f"Identity handler invalid {handler}")

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
