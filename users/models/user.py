import re
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar

import httpx
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.db.models import F, Manager, Q, Value
from django.db.models.functions import Concat, Lower
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from loguru import logger

from mastodon.models import EmailAccount, MastodonAccount, Platform, SocialAccount
from takahe.utils import Takahe

if TYPE_CHECKING:
    from mastodon.models import Mastodon

    from .apidentity import APIdentity
    from .preference import Preference

_RESERVED_USERNAMES = [
    "connect",
    "__",
    "admin",
    "administrator",
    "service",
    "support",
    "system",
    "user",
    "users",
    "api",
    "bot",
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
    social_accounts: "models.QuerySet[SocialAccount]"
    objects: ClassVar[UserManager] = UserManager()
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
    language = models.CharField(
        _("language"),
        max_length=10,
        choices=settings.LANGUAGES,
        null=False,
        default="en",
    )

    # remove the following
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
    mastodon_last_reachable = models.DateTimeField(default=timezone.now)
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
        indexes = [
            models.Index(fields=["mastodon_site", "mastodon_username"]),
        ]

    @cached_property
    def mastodon(self) -> "MastodonAccount | None":
        return MastodonAccount.objects.filter(user=self).first()

    @cached_property
    def email_account(self) -> "EmailAccount | None":
        return EmailAccount.objects.filter(user=self).first()

    @cached_property
    def mastodon_acct(self):
        return self.mastodon.handle if self.mastodon else ""

    @cached_property
    def locked(self):
        return self.identity.locked

    @property
    def display_name(self):
        return self.identity.display_name

    @property
    def avatar(self):
        return (
            self.identity.avatar if self.identity else settings.SITE_INFO["user_icon"]
        )

    @property
    def url(self):
        return reverse("journal:user_profile", args=[self.username])

    @property
    def absolute_url(self):
        return settings.SITE_INFO["site_url"] + self.url

    def __str__(self):
        return f'USER:{self.pk}:{self.username or "<missing>"}:{self.mastodon or self.email_account or ""}'

    @property
    def registration_complete(self):
        return self.username is not None

    @property
    def last_usage(self):
        from journal.models import ShelfMember

        p = (
            ShelfMember.objects.filter(owner=self.identity)
            .order_by("-edited_time")
            .first()
        )
        return p.edited_time if p else None

    def clear(self):
        if self.mastodon_site == "removed" and not self.is_active:
            return
        with transaction.atomic():
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
            SocialAccount.objects.filter(user=self).delete()

    def sync_relationship(self):
        from .apidentity import APIdentity

        def get_identity_ids(accts: list):
            return set(
                MastodonAccount.objects.filter(handle__in=accts).values_list(
                    "user__identity", flat=True
                )
            )

        def get_identity_ids_in_domains(domains: list):
            return set(
                MastodonAccount.objects.filter(domain__in=domains).values_list(
                    "user__identity", flat=True
                )
            )

        me = self.identity.pk
        if not self.mastodon:
            return
        for target_identity in get_identity_ids(self.mastodon.following):
            if not Takahe.get_is_following(me, target_identity):
                Takahe.follow(me, target_identity, True)

        for target_identity in get_identity_ids(self.mastodon.blocks):
            if not Takahe.get_is_blocking(me, target_identity):
                Takahe.block(me, target_identity)

        for target_identity in get_identity_ids_in_domains(self.mastodon.domain_blocks):
            if not Takahe.get_is_blocking(me, target_identity):
                Takahe.block(me, target_identity)

        for target_identity in get_identity_ids(self.mastodon.mutes):
            if not Takahe.get_is_muting(me, target_identity):
                Takahe.mute(me, target_identity)

    def sync_identity(self):
        identity = self.identity.takahe_identity
        if identity.deleted:
            logger.error(f"Identity {identity} is deleted, skip sync")
            return
        mastodon = self.mastodon
        if not mastodon:
            return
        identity.name = mastodon.display_name or identity.name or identity.username
        identity.summary = mastodon.note or identity.summary
        identity.manually_approves_followers = mastodon.locked
        if not bool(identity.icon) or identity.icon_uri != mastodon.avatar:
            identity.icon_uri = mastodon.avatar
            if identity.icon_uri:
                try:
                    r = httpx.get(identity.icon_uri)
                    f = ContentFile(r.content, name=identity.icon_uri.split("/")[-1])
                    identity.icon.save(f.name, f, save=False)
                except Exception as e:
                    logger.error(
                        f"fetch icon failed: {identity} {identity.icon_uri}",
                        extra={"exception": e},
                    )
        identity.save()

    def refresh_mastodon_data(self, skip_detail=False, sleep_hours=0):
        """Try refresh account data from mastodon server, return True if refreshed successfully"""
        mastodon = self.mastodon
        if not mastodon:
            return False
        if mastodon.last_refresh and mastodon.last_refresh > timezone.now() - timedelta(
            hours=sleep_hours
        ):
            logger.debug(f"Skip refreshing Mastodon data for {self}")
            return
        logger.debug(f"Refreshing Mastodon data for {self}")
        if not mastodon.check_alive():
            if (
                timezone.now() - self.mastodon_last_reachable
                > timedelta(days=settings.DEACTIVATE_AFTER_UNREACHABLE_DAYS)
                and not self.email
            ):
                logger.warning(f"Deactivate {self} bc unable to reach for too long")
                self.is_active = False
                self.save(update_fields=["is_active"])
                return False
        if not mastodon.refresh():
            return False
        if skip_detail:
            return True
        if not self.preference.mastodon_skip_userinfo:
            self.sync_identity()
        if not self.preference.mastodon_skip_relationship:
            mastodon.refresh_graph()
            self.sync_relationship()
        return True

    @cached_property
    def unread_announcements(self):
        from takahe.utils import Takahe

        return Takahe.get_announcements_for_user(self)

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
    def register(cls, **param) -> "User":
        from .preference import Preference

        account = param.pop("account", None)
        with transaction.atomic():
            if account:
                if account.platform == Platform.MASTODON:
                    param["mastodon_username"] = account.account_data["username"]
                    param["mastodon_site"] = account.domain
                    param["mastodon_id"] = account.account_data["id"]
                elif account.platform == Platform.EMAIL:
                    param["email"] = account.handle
            new_user = cls(**param)
            if not new_user.username:
                raise ValueError("username is not set")
            if "language" not in param:
                new_user.language = translation.get_language()
            new_user.save()
            Preference.objects.create(user=new_user)
            if account:
                account.user = new_user
                account.save()
            Takahe.init_identity_for_local_user(new_user)
            new_user.identity.shelf_manager
            if new_user.mastodon:
                Takahe.fetch_remote_identity(new_user.mastodon.handle)
            return new_user


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
