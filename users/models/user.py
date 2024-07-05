import re
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar

import django_rq
import httpx
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from loguru import logger

from mastodon.models import (
    BlueskyAccount,
    EmailAccount,
    MastodonAccount,
    SocialAccount,
    ThreadsAccount,
)
from takahe.utils import Takahe

if TYPE_CHECKING:
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
        from mastodon.models import Email

        Takahe.get_domain()  # ensure configuration is complete

        user = User.register(username=username)
        e = Email.new_account(email)
        if not e:
            raise ValueError("Invalid Email")
        e.user = user
        e.save()
        return user

    def create_superuser(self, username, email, password=None):
        from mastodon.models import Email
        from takahe.models import User as TakaheUser

        Takahe.get_domain()  # ensure configuration is complete
        user = User.register(username=username, is_superuser=True)
        e = Email.new_account(email)
        if not e:
            raise ValueError("Invalid Email")
        e.user = user
        e.save()
        tu = TakaheUser.objects.get(pk=user.pk, email="@" + username)
        tu.admin = True
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
        ]
        indexes = [models.Index("is_active", name="index_user_is_active")]

    @cached_property
    def mastodon(self) -> "MastodonAccount | None":
        return MastodonAccount.objects.filter(user=self).first()

    @cached_property
    def threads(self) -> "ThreadsAccount | None":
        return ThreadsAccount.objects.filter(user=self).first()

    @cached_property
    def bluesky(self) -> "BlueskyAccount | None":
        return BlueskyAccount.objects.filter(user=self).first()

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
        return f'{self.pk}:{self.username or "<missing>"}'

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
        if not self.is_active:
            return
        with transaction.atomic():
            accts = [str(a) for a in self.social_accounts.all()]
            self.first_name = (";").join(accts)
            self.last_name = self.username
            self.is_active = False
            # self.username = "~removed~" + str(self.pk)
            # to get ready for federation, username has to be reserved
            self.save()
            self.identity.deleted = timezone.now()
            self.identity.save()
            self.social_accounts.all().delete()

    def sync_relationship(self):
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
        """sync display name, bio, and avatar from available sources"""
        identity = self.identity.takahe_identity
        if identity.deleted:
            logger.error(f"Identity {identity} is deleted, skip sync")
            return
        mastodon = self.mastodon
        threads = self.threads
        bluesky = self.bluesky
        changed = False
        name = (
            (mastodon.display_name if mastodon else "")
            or (threads.username if threads else "")
            or (bluesky.display_name if bluesky else "")
            or identity.name
            or identity.username
        )
        if identity.name != name:
            identity.name = name
            changed = True
        summary = (
            (mastodon.note if mastodon else "")
            or (threads.threads_biography if threads else "")
            or (bluesky.description if bluesky else "")
            or identity.summary
        )
        if identity.summary != summary:
            identity.summary = summary
            changed = True
        identity.manually_approves_followers = (
            mastodon.locked if mastodon else identity.manually_approves_followers
        )
        # it's tedious to update avatar repeatedly, so only sync it once
        if not identity.icon:
            url = None
            if mastodon and mastodon.avatar:
                url = mastodon.avatar
            elif threads and threads.threads_profile_picture_url:
                url = threads.threads_profile_picture_url
            elif bluesky and bluesky.avatar:
                url = bluesky.avatar
            if url:
                try:
                    r = httpx.get(url)
                except Exception as e:
                    logger.error(
                        f"fetch icon failed: {identity} {url}",
                        extra={"exception": e},
                    )
                    r = None
                if r:
                    name = str(self.pk) + "-" + url.split("/")[-1].split("?")[0][-100:]
                    f = ContentFile(r.content, name=name)
                    identity.icon.save(name, f, save=False)
                    changed = True
        if changed:
            identity.save()
            Takahe.update_state(identity, "outdated")

    def sync_accounts(self, skip_graph=False, sleep_hours=0):
        """Try refresh account data from 3p server"""
        for account in self.social_accounts.all():
            account.sync(skip_graph=skip_graph, sleep_hours=sleep_hours)
        if not self.preference.mastodon_skip_userinfo:
            self.sync_identity()
        if skip_graph:
            return
        if not self.preference.mastodon_skip_relationship:
            self.sync_relationship()
        return

    @staticmethod
    def sync_accounts_task(user_id):
        user = User.objects.get(pk=user_id)
        logger.info(f"{user} accounts sync start")
        if user.sync_accounts():
            logger.info(f"{user} accounts sync done")
        else:
            logger.warning(f"{user} accounts sync failed")

    def sync_accounts_later(self):
        django_rq.get_queue("mastodon").enqueue(User.sync_accounts_task, self.pk)

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
            new_user = cls(**param)
            if not new_user.username:
                raise ValueError("username is not set")
            if "language" not in param:
                new_user.language = translation.get_language()
            new_user.set_unusable_password()
            new_user.save()
            Preference.objects.create(user=new_user)
            if account:
                account.user = new_user
                account.save()
            Takahe.init_identity_for_local_user(new_user)
            new_user.identity.shelf_manager
            return new_user

    def reconnect_account(self, account: SocialAccount):
        with transaction.atomic():
            SocialAccount.objects.filter(user=self, type=account.type).delete()
            account.user = self
            account.save()


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
