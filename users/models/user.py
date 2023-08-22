import hashlib
import re
from functools import cached_property
from typing import TYPE_CHECKING

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Manager, Q, Value
from django.db.models.functions import Concat, Lower
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from loguru import logger

from management.models import Announcement
from mastodon.api import *
from takahe.utils import Takahe

if TYPE_CHECKING:
    from .apidentity import APIdentity
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


class UserManager(BaseUserManager):
    def create_user(self, username, email, password=None):
        Takahe.get_domain()  # ensure configuration is complete
        user = User.register(username=username, email=email)
        return user

    def create_superuser(self, username, email, password=None):
        from takahe.models import User as TakaheUser

        Takahe.get_domain()  # ensure configuration is complete
        user = User.register(username=username, email=email, is_superuser=True)
        tu = TakaheUser.objects.get(pk=user.pk, email="@" + username)
        tu.admin = True
        tu.set_password(password)
        tu.save()
        return user


class User(AbstractUser):
    identity: "APIdentity"
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
    objects = UserManager()

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
        return self.identity.avatar if self.identity else static("img/avatar.svg")

    @property
    def handler(self):
        return (
            f"{self.username}" if self.username else self.mastodon_acct or f"~{self.pk}"
        )

    @property
    def url(self):
        return reverse("journal:user_profile", args=[self.handler])

    def __str__(self):
        return f'{self.pk}:{self.username or ""}:{self.mastodon_acct}'

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
        self.save()
        self.identity.deleted = timezone.now()
        self.identity.save()

    def sync_relationships(self):
        # FIXME
        pass

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
            self.sync_relationships()
            updated = True
        elif code == 401:
            logger.error(f"Refresh mastodon data error 401 for {self}")
            self.mastodon_token = ""
        return updated

    @property
    def unread_announcements(self):
        unread_announcements = Announcement.objects.filter(
            pk__gt=self.read_announcement_index
        ).order_by("-pk")
        return unread_announcements

    @property
    def activity_manager(self):
        if not self.identity:
            raise ValueError("User has no identity")
        return self.identity.activity_manager

    @property
    def shelf_manager(self):
        if not self.identity:
            raise ValueError("User has no identity")
        return self.identity.shelf_manager

    @property
    def tag_manager(self):
        if not self.identity:
            raise ValueError("User has no identity")
        return self.identity.tag_manager

    @classmethod
    def get(cls, name, case_sensitive=False):
        if isinstance(name, str):
            if name.startswith("~"):
                try:
                    query_kwargs = {"pk": int(name[1:])}
                except:
                    return None
            elif name.startswith("@"):
                query_kwargs = {
                    "username__iexact" if case_sensitive else "username": name[1:]
                }
            else:
                sp = name.split("@")
                if len(sp) == 2:
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

    @classmethod
    def register(cls, **param):
        from .preference import Preference

        new_user = cls(**param)
        new_user.save()
        Preference.objects.create(user=new_user)
        if new_user.username:  # TODO make username required in registeration
            new_user.initialize()
        return new_user

    def identity_linked(self):
        from .apidentity import APIdentity

        return APIdentity.objects.filter(user=self).exists()

    def initialize(self):
        Takahe.init_identity_for_local_user(self)
        self.identity.shelf_manager


# TODO the following models should be deprecated soon


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
