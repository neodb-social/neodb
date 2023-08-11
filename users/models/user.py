import hashlib
import re
from functools import cached_property
from typing import TYPE_CHECKING

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Concat, Lower
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from loguru import logger

from management.models import Announcement
from mastodon.api import *

if TYPE_CHECKING:
    from .preference import Preference

_RESERVED_USERNAMES = [
    "connect",
    "oauth2_login",
    "__",
    "admin",
    "api",
    "me",
]


@deconstructible
class UsernameValidator(UnicodeUsernameValidator):
    regex = r"^[a-zA-Z0-9_]{2,30}$"
    message = _(
        "Enter a valid username. This value may contain only unaccented lowercase a-z and uppercase A-Z letters, numbers, and _ characters."
    )
    flags = re.ASCII

    def __call__(self, value):
        if value and value.lower() in _RESERVED_USERNAMES:
            raise ValidationError(self.message, code=self.code)
        return super().__call__(value)


class User(AbstractUser):
    preference: "Preference"
    username_validator = UsernameValidator()
    username = models.CharField(
        _("username"),
        max_length=100,
        unique=True,
        null=True,  # allow null for newly registered users who has not set a user name
        help_text=_("Required. 50 characters or fewer. Letters, digits and _ only."),
        validators=[username_validator],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )
    email = models.EmailField(
        _("email address"),
        unique=True,
        default=None,
        null=True,
    )
    pending_email = models.EmailField(
        _("email address pending verification"), default=None, null=True
    )
    local_following = models.ManyToManyField(
        through="Follow",
        to="self",
        through_fields=("owner", "target"),
        symmetrical=False,
        related_name="local_followers",
    )
    local_blocking = models.ManyToManyField(
        through="Block",
        to="self",
        through_fields=("owner", "target"),
        symmetrical=False,
        related_name="local_blocked_by",
    )
    local_muting = models.ManyToManyField(
        through="Mute",
        to="self",
        through_fields=("owner", "target"),
        symmetrical=False,
        related_name="+",
    )
    following = models.JSONField(default=list)
    muting = models.JSONField(default=list)
    # rejecting = local/external blocking + local/external blocked_by + domain_blocking + domain_blocked_by
    rejecting = models.JSONField(default=list)
    mastodon_id = models.CharField(max_length=100, default=None, null=True)
    mastodon_username = models.CharField(max_length=100, default=None, null=True)
    mastodon_site = models.CharField(max_length=100, default=None, null=True)
    mastodon_token = models.CharField(max_length=2048, default="")
    mastodon_refresh_token = models.CharField(max_length=2048, default="")
    mastodon_locked = models.BooleanField(default=False)
    mastodon_followers = models.JSONField(default=list)
    mastodon_following = models.JSONField(default=list)
    mastodon_mutes = models.JSONField(default=list)
    mastodon_blocks = models.JSONField(default=list)
    mastodon_domain_blocks = models.JSONField(default=list)
    mastodon_account = models.JSONField(default=dict)
    mastodon_last_refresh = models.DateTimeField(default=timezone.now)
    # store the latest read announcement id,
    # every time user read the announcement update this field
    read_announcement_index = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("username"),
                name="unique_username",
            ),
            models.UniqueConstraint(
                Lower("email"),
                name="unique_email",
            ),
            models.UniqueConstraint(
                Lower("mastodon_username"),
                Lower("mastodon_site"),
                name="unique_mastodon_username",
            ),
            models.UniqueConstraint(
                Lower("mastodon_id"),
                Lower("mastodon_site"),
                name="unique_mastodon_id",
            ),
            models.CheckConstraint(
                check=(
                    Q(is_active=False)
                    | Q(mastodon_username__isnull=False)
                    | Q(email__isnull=False)
                ),
                name="at_least_one_login_method",
            ),
        ]

    @staticmethod
    def register(**param):
        from .preference import Preference

        new_user = User(**param)
        new_user.save()
        Preference.objects.create(user=new_user)
        return new_user

    @cached_property
    def mastodon_acct(self):
        return (
            f"{self.mastodon_username}@{self.mastodon_site}"
            if self.mastodon_username
            else ""
        )

    @property
    def locked(self):
        return self.mastodon_locked

    @property
    def display_name(self):
        return (
            (self.mastodon_account.get("display_name") if self.mastodon_account else "")
            or self.username
            or self.mastodon_acct
            or ""
        )

    @property
    def avatar(self):
        if self.mastodon_account:
            return self.mastodon_account.get("avatar") or static("img/avatar.svg")
        if self.email:
            return (
                "https://www.gravatar.com/avatar/"
                + hashlib.md5(self.email.lower().encode()).hexdigest()
            )
        return static("img/avatar.svg")

    @property
    def handler(self):
        return self.mastodon_acct or self.username or f"~{self.pk}"

    @property
    def url(self):
        return reverse("journal:user_profile", args=[self.handler])

    def __str__(self):
        return f'{self.pk}:{self.username or ""}:{self.mastodon_acct}'

    @property
    def ignoring(self):
        return self.muting + self.rejecting

    def follow(self, target: "User"):
        if (
            target is None
            or target.locked
            or self.is_following(target)
            or self.is_blocking(target)
            or self.is_blocked_by(target)
        ):
            return False
        self.local_following.add(target)
        self.following.append(target.pk)
        self.save(update_fields=["following"])
        return True

    def unfollow(self, target: "User"):
        if target and target in self.local_following.all():
            self.local_following.remove(target)
            if (
                target.pk in self.following
                and target.mastodon_acct not in self.mastodon_following
            ):
                self.following.remove(target.pk)
                self.save(update_fields=["following"])
            return True
        return False

    def remove_follower(self, target: "User"):
        if target is None or self not in target.local_following.all():
            return False
        target.local_following.remove(self)
        if (
            self.pk in target.following
            and self.mastodon_acct not in target.mastodon_following
        ):
            target.following.remove(self.pk)
            target.save(update_fields=["following"])
        return True

    def block(self, target: "User"):
        if target is None or target in self.local_blocking.all():
            return False
        self.local_blocking.add(target)
        if target.pk in self.following:
            self.following.remove(target.pk)
            self.save(update_fields=["following"])
        if self.pk in target.following:
            target.following.remove(self.pk)
            target.save(update_fields=["following"])
        if target in self.local_following.all():
            self.local_following.remove(target)
        if self in target.local_following.all():
            target.local_following.remove(self)
        if target.pk not in self.rejecting:
            self.rejecting.append(target.pk)
            self.save(update_fields=["rejecting"])
        if self.pk not in target.rejecting:
            target.rejecting.append(self.pk)
            target.save(update_fields=["rejecting"])
        return True

    def unblock(self, target: "User"):
        if target and target in self.local_blocking.all():
            self.local_blocking.remove(target)
            if not self.is_blocked_by(target):
                if target.pk in self.rejecting:
                    self.rejecting.remove(target.pk)
                    self.save(update_fields=["rejecting"])
                if self.pk in target.rejecting:
                    target.rejecting.remove(self.pk)
                    target.save(update_fields=["rejecting"])
            return True
        return False

    def mute(self, target: "User"):
        if (
            target is None
            or target in self.local_muting.all()
            or target.mastodon_acct in self.mastodon_mutes
        ):
            return False
        self.local_muting.add(target)
        if target.pk not in self.muting:
            self.muting.append(target.pk)
        self.save()
        return True

    def unmute(self, target: "User"):
        if target and target in self.local_muting.all():
            self.local_muting.remove(target)
            if target.pk in self.muting:
                self.muting.remove(target.pk)
                self.save()
            return True
        return False

    def clear(self):
        if self.mastodon_site == "removed" and not self.is_active:
            return
        self.first_name = self.mastodon_acct or ""
        self.last_name = self.email or ""
        self.is_active = False
        self.email = None
        # self.username = "~removed~" + str(self.pk)
        # to get ready for federation, username has to be reserved
        self.mastodon_username = None
        self.mastodon_id = None
        self.mastodon_site = "removed"
        self.mastodon_token = ""
        self.mastodon_locked = False
        self.mastodon_followers = []
        self.mastodon_following = []
        self.mastodon_mutes = []
        self.mastodon_blocks = []
        self.mastodon_domain_blocks = []
        self.mastodon_account = {}

    def merge_relationships(self):
        self.muting = self.merged_muting_ids()
        self.rejecting = self.merged_rejecting_ids()
        # caculate following after rejecting is merged
        self.following = self.merged_following_ids()

    @classmethod
    def merge_rejected_by(cls):
        """
        Caculate rejecting field to include blocked by for external users
        Should be invoked after invoking merge_relationships() for all users
        """
        # FIXME this is quite inifficient, should only invoked in async task
        external_users = list(
            cls.objects.filter(mastodon_username__isnull=False, is_active=True)
        )
        reject_changed = []
        follow_changed = []
        for u in external_users:
            for v in external_users:
                if v.pk in u.rejecting and u.pk not in v.rejecting:
                    v.rejecting.append(u.pk)
                    if v not in reject_changed:
                        reject_changed.append(v)
                    if u.pk in v.following:
                        v.following.remove(u.pk)
                        if v not in follow_changed:
                            follow_changed.append(v)
        for u in reject_changed:
            u.save(update_fields=["rejecting"])
        for u in follow_changed:
            u.save(update_fields=["following"])
        return len(follow_changed) + len(reject_changed)

    def refresh_mastodon_data(self):
        """Try refresh account data from mastodon server, return true if refreshed successfully, note it will not save to db"""
        self.mastodon_last_refresh = timezone.now()
        code, mastodon_account = verify_account(self.mastodon_site, self.mastodon_token)
        if code == 401 and self.mastodon_refresh_token:
            self.mastodon_token = refresh_access_token(
                self.mastodon_site, self.mastodon_refresh_token
            )
            if self.mastodon_token:
                code, mastodon_account = verify_account(
                    self.mastodon_site, self.mastodon_token
                )
        updated = False
        if mastodon_account:
            self.mastodon_account = mastodon_account
            self.mastodon_locked = mastodon_account["locked"]
            if self.mastodon_username != mastodon_account["username"]:
                logger.warning(
                    f"username changed from {self} to {mastodon_account['username']}"
                )
                self.mastodon_username = mastodon_account["username"]
            # self.mastodon_token = token
            # user.mastodon_id  = mastodon_account['id']
            self.mastodon_followers = get_related_acct_list(
                self.mastodon_site,
                self.mastodon_token,
                f"/api/v1/accounts/{self.mastodon_id}/followers",
            )
            self.mastodon_following = get_related_acct_list(
                self.mastodon_site,
                self.mastodon_token,
                f"/api/v1/accounts/{self.mastodon_id}/following",
            )
            self.mastodon_mutes = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/mutes"
            )
            self.mastodon_blocks = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/blocks"
            )
            self.mastodon_domain_blocks = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/domain_blocks"
            )
            self.merge_relationships()
            updated = True
        elif code == 401:
            logger.error(f"Refresh mastodon data error 401 for {self}")
            self.mastodon_token = ""
        return updated

    def merged_following_ids(self):
        fl = []
        for m in self.mastodon_following:
            target = User.get(m)
            if target and (
                (not target.mastodon_locked)
                or self.mastodon_acct in target.mastodon_followers
            ):
                fl.append(target.pk)
        for user in self.local_following.all():
            if user.pk not in fl and not user.locked and not user.is_blocking(self):
                fl.append(user.pk)
        fl = [x for x in fl if x not in self.rejecting]
        return sorted(fl)

    def merged_muting_ids(self):
        external_muting_user_ids = list(
            User.objects.all()
            .annotate(acct=Concat("mastodon_username", Value("@"), "mastodon_site"))
            .filter(acct__in=self.mastodon_mutes)
            .values_list("pk", flat=True)
        )
        l = list(
            set(
                external_muting_user_ids
                + list(self.local_muting.all().values_list("pk", flat=True))
            )
        )
        return sorted(l)

    def merged_rejecting_ids(self):
        domain_blocked_user_ids = list(
            User.objects.filter(
                mastodon_site__in=self.mastodon_domain_blocks
            ).values_list("pk", flat=True)
        )
        external_blocking_user_ids = list(
            User.objects.all()
            .annotate(acct=Concat("mastodon_username", Value("@"), "mastodon_site"))
            .filter(acct__in=self.mastodon_blocks)
            .values_list("pk", flat=True)
        )
        l = list(
            set(
                domain_blocked_user_ids
                + external_blocking_user_ids
                + list(self.local_blocking.all().values_list("pk", flat=True))
                + list(self.local_blocked_by.all().values_list("pk", flat=True))  # type: ignore
                + list(self.local_muting.all().values_list("pk", flat=True))
            )
        )
        return sorted(l)

    def is_blocking(self, target):
        return (
            (
                target in self.local_blocking.all()
                or target.mastodon_acct in self.mastodon_blocks
                or target.mastodon_site in self.mastodon_domain_blocks
            )
            if target.is_authenticated
            else self.preference.no_anonymous_view
        )

    def is_blocked_by(self, target):
        return target.is_authenticated and target.is_blocking(self)

    def is_muting(self, target):
        return target.pk in self.muting or target.mastodon_acct in self.mastodon_mutes

    def is_following(self, target):
        return (
            self.mastodon_acct in target.mastodon_followers
            if target.locked
            else target.pk in self.following
            # or target.mastodon_acct in self.mastodon_following
            # or self.mastodon_acct in target.mastodon_followers
        )

    def is_followed_by(self, target):
        return target.is_following(self)

    def get_mark_for_item(self, item):
        params = {item.__class__.__name__.lower() + "_id": item.id, "owner": self}
        mark = item.mark_class.objects.filter(**params).first()
        return mark

    def get_max_visibility(self, viewer):
        if not viewer.is_authenticated:
            return 0
        elif viewer == self:
            return 2
        elif viewer.is_blocked_by(self):
            return -1
        elif viewer.is_following(self):
            return 1
        else:
            return 0

    @property
    def unread_announcements(self):
        unread_announcements = Announcement.objects.filter(
            pk__gt=self.read_announcement_index
        ).order_by("-pk")
        return unread_announcements

    @classmethod
    def get(cls, name, case_sensitive=False):
        if isinstance(name, str):
            sp = name.split("@")
            if name.startswith("~"):
                try:
                    query_kwargs = {"pk": int(name[1:])}
                except:
                    return None
            elif len(sp) == 1:
                query_kwargs = {
                    "username__iexact" if case_sensitive else "username": name
                }
            elif len(sp) == 2:
                query_kwargs = {
                    "mastodon_username__iexact"
                    if case_sensitive
                    else "mastodon_username": sp[0],
                    "mastodon_site__iexact"
                    if case_sensitive
                    else "mastodon_site": sp[1],
                }
            else:
                return None
        elif isinstance(name, int):
            query_kwargs = {"pk": name}
        else:
            return None
        return User.objects.filter(**query_kwargs).first()

    @property
    def tags(self):
        from journal.models import TagManager

        return TagManager.all_tags_for_user(self)

    @cached_property
    def tag_manager(self):
        from journal.models import TagManager

        return TagManager.get_manager_for_user(self)

    @cached_property
    def shelf_manager(self):
        from journal.models import ShelfManager

        return ShelfManager.get_manager_for_user(self)

    @cached_property
    def activity_manager(self):
        from social.models import ActivityManager

        return ActivityManager.get_manager_for_user(self)


class Follow(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)


class Block(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)


class Mute(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)
