from functools import cached_property

from django.conf import settings
from django.db import models
from django.templatetags.static import static

from mastodon.models.mastodon import MastodonAccount
from takahe.utils import Takahe

from .preference import Preference
from .user import User


class APIdentity(models.Model):
    """
    An identity/actor in ActivityPub service.

    This model is used as 1:1 mapping to Takahe Identity Model
    """

    user: User
    user_id: int
    user = models.OneToOneField(
        User, models.SET_NULL, related_name="identity", null=True
    )  # type:ignore
    local = models.BooleanField()
    username = models.CharField(max_length=500, blank=True, null=True)
    domain_name = models.CharField(max_length=500, blank=True, null=True)
    deleted = models.DateTimeField(null=True, blank=True)
    anonymous_viewable = models.BooleanField(null=False, default=True)

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
            self.user.is_active if self.user else self.takahe_identity.deleted is None
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
                else self.takahe_identity.icon_uri or settings.SITE_INFO["user_icon"]
            )
        else:
            return f"/proxy/identity_icon/{self.pk}/"

    @property
    def url(self):
        return f"/users/{self.handle}/" if self.local else f"/users/@{self.handle}/"

    @property
    def preference(self):
        return self.user.preference if self.user else Preference()

    @property
    def full_handle(self):
        return f"{self.username}@{self.domain_name}"

    @property
    def handle(self):
        if self.local:
            return self.username
        else:
            return f"{self.username}@{self.domain_name}"

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
        return APIdentity.objects.filter(pk__in=self.following_requests)

    @property
    def rejecting(self):
        return Takahe.get_rejecting_ids(self.pk)

    @property
    def ignoring(self):
        return self.muting + self.rejecting

    def follow(self, target: "APIdentity", force_accept: bool = False):
        Takahe.follow(self.pk, target.pk, force_accept)

    def unfollow(self, target: "APIdentity"):  # this also cancels follow request
        Takahe.unfollow(self.pk, target.pk)

    @property
    def requested_followers(self):
        return Takahe.get_requested_follower_ids(self.pk)

    @property
    def following_requests(self):
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
        return Takahe.get_is_blocking(self.pk, target.pk)

    def is_blocked_by(self, target: "APIdentity"):
        return Takahe.get_is_blocking(target.pk, self.pk)

    def is_muting(self, target: "APIdentity"):
        return Takahe.get_is_muting(self.pk, target.pk)

    def is_following(self, target: "APIdentity"):
        return Takahe.get_is_following(self.pk, target.pk)

    def is_followed_by(self, target: "APIdentity"):
        return Takahe.get_is_following(target.pk, self.pk)

    def is_requesting(self, target: "APIdentity"):
        return Takahe.get_is_follow_requesting(self.pk, target.pk)

    def is_requested(self, target: "APIdentity"):
        return Takahe.get_is_follow_requesting(target.pk, self.pk)

    @classmethod
    def get_remote(cls, username, domain):
        i = cls.objects.filter(
            username__iexact=username, domain_name__iexact=domain, deleted__isnull=True
        ).first()
        if i:
            return i
        if domain != settings.SITE_DOMAIN:
            identity = Takahe.get_identity_by_handler(username, domain)
            if identity:
                return Takahe.get_or_create_remote_apidentity(identity)

    @classmethod
    def get_by_handle(cls, handler: str, match_linked=False) -> "APIdentity":
        """
        Handler format
        'id' - local identity with username 'id'
        'id@site'
            match_linked = True - local identity with linked mastodon id == 'id@site' (for backward compatibility)
            match_linked = False - remote activitypub identity 'id@site'
        '@id' - local identity with username 'id'
        '@id@site' - remote activitypub identity 'id@site'
        """
        s = handler.split("@")
        sl = len(s)
        if sl == 1 or (sl == 2 and s[0] == ""):
            return cls.objects.get(
                username__iexact=s[0] if sl == 1 else s[1],
                local=True,
                deleted__isnull=True,
            )
        elif sl == 2:
            if match_linked:
                i = MastodonAccount.objects.get(
                    handle__iexact=handler,
                ).user.identity
                if i.deleted:
                    raise cls.DoesNotExist(f"Identity deleted {handler}")
                return i
            else:
                i = cls.get_remote(s[0], s[1])
                if i:
                    return i
                raise cls.DoesNotExist(f"Identity not found {handler}")
        elif sl == 3 and s[0] == "":
            i = cls.get_remote(s[1], s[2])
            if i:
                return i
            raise cls.DoesNotExist(f"Identity not found {handler}")
        else:
            raise cls.DoesNotExist(f"Identity handle invalid {handler}")

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
