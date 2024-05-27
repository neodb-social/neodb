import datetime
import os
import random
import re
import secrets
import ssl
import time
from datetime import date
from functools import cached_property, partial
from typing import TYPE_CHECKING, Literal, Optional
from urllib.parse import urlparse

import httpx
import urlman
from cachetools import TTLCache, cached
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.files.storage import FileSystemStorage
from django.db import models, transaction
from django.template.defaultfilters import linebreaks_filter
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from loguru import logger
from lxml import etree

from .html import ContentRenderer, FediverseHtmlParser
from .uris import *

if TYPE_CHECKING:
    from django_stubs_ext.db.models.manager import RelatedManager

_migration_mode = False


def set_migration_mode(disable: bool):
    global _migration_mode
    _migration_mode = disable


# class TakaheSession(models.Model):
#     session_key = models.CharField(_("session key"), max_length=40, primary_key=True)
#     session_data = models.TextField(_("session data"))
#     expire_date = models.DateTimeField(_("expire date"), db_index=True)

#     class Meta:
#         db_table = "django_session"


class Snowflake:
    """
    Snowflake ID generator and parser.
    """

    # Epoch is 2022/1/1 at midnight, as these are used for _created_ times in our
    # own database, not original publish times (which would need an earlier one)
    EPOCH = 1641020400

    TYPE_POST = 0b000
    TYPE_POST_INTERACTION = 0b001
    TYPE_IDENTITY = 0b010
    TYPE_REPORT = 0b011
    TYPE_FOLLOW = 0b100

    @classmethod
    def generate(cls, type_id: int) -> int:
        """
        Generates a snowflake-style ID for the given "type". They are designed
        to fit inside 63 bits (a signed bigint)

        ID layout is:
        * 41 bits of millisecond-level timestamp (enough for EPOCH + 69 years)
        * 19 bits of random data (1% chance of clash at 10000 per millisecond)
        * 3 bits of type information

        We use random data rather than a sequence ID to try and avoid pushing
        this job onto the DB - we may do that in future. If a clash does
        occur, the insert will fail and Stator will retry the work for anything
        that's coming in remotely, leaving us to just handle that scenario for
        our own posts, likes, etc.
        """
        # Get the current time in milliseconds
        now: int = int((time.time() - cls.EPOCH) * 1000)
        # Generate random data
        rand_seq: int = secrets.randbits(19)
        # Compose them together
        return (now << 22) | (rand_seq << 3) | type_id

    @classmethod
    def generate_post_at(cls, t: float) -> int:
        """
        Generates a snowflake-style ID for post at given time

        post time before EPOCH (2022) will be mixed in with Jan 2022
        """
        if t > cls.EPOCH:
            now: int = int((t - cls.EPOCH) * 1000)
        else:
            now = int(t) if t > 0 else 0
        # Generate random data
        rand_seq: int = secrets.randbits(19)
        # Compose them together
        return (now << 22) | (rand_seq << 3) | cls.TYPE_POST

    @classmethod
    def get_type(cls, snowflake: int) -> int:
        """
        Returns the type of a given snowflake ID
        """
        if snowflake < (1 << 22):
            raise ValueError("Not a valid Snowflake ID")
        return snowflake & 0b111

    @classmethod
    def get_time(cls, snowflake: int) -> float:
        """
        Returns the generation time (in UNIX timestamp seconds) of the ID
        """
        if snowflake < (1 << 22):
            raise ValueError("Not a valid Snowflake ID")
        return ((snowflake >> 22) / 1000) + cls.EPOCH

    # Handy pre-baked methods for django model defaults
    @classmethod
    def generate_post(cls) -> int:
        return cls.generate(cls.TYPE_POST)

    @classmethod
    def generate_post_interaction(cls) -> int:
        return cls.generate(cls.TYPE_POST_INTERACTION)

    @classmethod
    def generate_identity(cls) -> int:
        return cls.generate(cls.TYPE_IDENTITY)

    @classmethod
    def generate_report(cls) -> int:
        return cls.generate(cls.TYPE_REPORT)

    @classmethod
    def generate_follow(cls) -> int:
        return cls.generate(cls.TYPE_FOLLOW)


class RsaKeys:
    @classmethod
    def generate_keypair(cls) -> tuple[str, str]:
        """
        Generates a new RSA keypair
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        private_key_serialized = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        public_key_serialized = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        return private_key_serialized, public_key_serialized


class Invite(models.Model):
    """
    An invite token, good for one signup.
    """

    class Meta:
        # managed = False
        db_table = "users_invite"

    # Should always be lowercase
    token = models.CharField(max_length=500, unique=True)

    # Admin note about this code
    note = models.TextField(null=True, blank=True)

    # Uses remaining (null means "infinite")
    uses = models.IntegerField(null=True, blank=True)

    # Expiry date
    expires = models.DateTimeField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    @classmethod
    def create_random(cls, uses=None, expires=None, note=None):
        return cls.objects.create(
            token="".join(
                random.choice("abcdefghkmnpqrstuvwxyz23456789") for i in range(20)
            ),
            uses=uses,
            expires=expires,
            note=note,
        )

    @property
    def valid(self):
        if self.uses is not None:
            if self.uses <= 0:
                return False
        if self.expires is not None:
            return self.expires >= timezone.now()
        return True


class User(AbstractBaseUser):
    if TYPE_CHECKING:
        identities: RelatedManager["Identity"]

    class Meta:
        # managed = False
        db_table = "users_user"

    email = models.EmailField(unique=True)
    admin = models.BooleanField(default=False)
    moderator = models.BooleanField(default=False)
    banned = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    @property
    def is_active(self):
        return not (self.deleted or self.banned)

    @property
    def is_superuser(self):
        return self.admin

    @property
    def is_staff(self):
        return self.admin

    def has_module_perms(self, module):
        return self.admin

    def has_perm(self, perm):
        return self.admin

    # @cached_property
    # def config_user(self) -> Config.UserOptions:
    #     return Config.load_user(self)


class Domain(models.Model):
    """
    Represents a domain that a user can have an account on.

    For protocol reasons, if we want to allow custom usernames
    per domain, each "display" domain (the one in the handle) must either let
    us serve on it directly, or have a "service" domain that maps
    to it uniquely that we can serve on that.

    That way, someone coming in with just an Actor URI as their
    entrypoint can still try to webfinger preferredUsername@actorDomain
    and we can return an appropriate response.

    It's possible to just have one domain do both jobs, of course.
    This model also represents _other_ servers' domains, which we treat as
    display domains for now, until we start doing better probing.
    """

    domain = models.CharField(max_length=250, primary_key=True)
    service_domain = models.CharField(
        max_length=250,
        null=True,
        blank=True,
        db_index=True,
        unique=True,
    )

    # state = StateField(DomainStates)
    state = models.CharField(max_length=100, default="outdated")
    state_changed = models.DateTimeField(auto_now_add=True)

    # nodeinfo 2.0 detail about the remote server
    nodeinfo = models.JSONField(null=True, blank=True)

    # If we own this domain
    local = models.BooleanField()

    # If we have blocked this domain from interacting with us
    blocked = models.BooleanField(default=False)

    # Domains can be joinable by any user of the instance (as the default one
    # should)
    public = models.BooleanField(default=False)

    # If this is the default domain (shown as the default entry for new users)
    default = models.BooleanField(default=False)

    # Domains can also be linked to one or more users for their private use
    # This should be display domains ONLY
    users = models.ManyToManyField("takahe.User", related_name="domains", blank=True)

    # Free-form notes field for admins
    notes = models.TextField(blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class urls(urlman.Urls):
        root = "/admin/domains/"
        create = "/admin/domains/create/"
        edit = "/admin/domains/{self.domain}/"
        delete = "{edit}delete/"
        root_federation = "/admin/federation/"
        edit_federation = "/admin/federation/{self.domain}/"

    class Meta:
        # managed = False
        db_table = "users_domain"
        indexes: list = []

    @classmethod
    def get_remote_domain(cls, domain: str) -> "Domain":
        return cls.objects.get_or_create(domain=domain.lower(), local=False)[0]

    @classmethod
    def get_domain(cls, domain: str) -> Optional["Domain"]:
        try:
            return cls.objects.get(
                models.Q(domain=domain.lower())
                | models.Q(service_domain=domain.lower())
            )
        except cls.DoesNotExist:
            return None

    @property
    def uri_domain(self) -> str:
        if self.service_domain:
            return self.service_domain
        return self.domain

    @classmethod
    def available_for_user(cls, user):
        """
        Returns domains that are available for the user to put an identity on
        """
        return cls.objects.filter(
            models.Q(public=True) | models.Q(users__id=user.id),
            local=True,
        ).order_by("-default", "domain")

    def __str__(self):
        return self.domain


def upload_store():
    return FileSystemStorage(
        location=settings.TAKAHE_MEDIA_ROOT, base_url=settings.TAKAHE_MEDIA_URL
    )


def upload_namer(prefix, instance, filename):
    """
    Names uploaded images.

    By default, obscures the original name with a random UUID.
    """
    _, old_extension = os.path.splitext(filename)
    new_filename = secrets.token_urlsafe(20)
    now = timezone.now()
    return f"{prefix}/{now.year}/{now.month}/{now.day}/{new_filename}{old_extension}"


class Identity(models.Model):
    """
    Represents both local and remote Fediverse identities (actors)
    """

    domain_id: str

    class Restriction(models.IntegerChoices):
        none = 0
        limited = 1
        blocked = 2

    ACTOR_TYPES = ["person", "service", "application", "group", "organization"]

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_identity)

    # The Actor URI is essentially also a PK - we keep the default numeric
    # one around as well for making nice URLs etc.
    actor_uri = models.CharField(max_length=500, unique=True)

    # state = StateField(IdentityStates)
    state = models.CharField(max_length=100, default="outdated")
    state_changed = models.DateTimeField(auto_now_add=True)
    state_next_attempt = models.DateTimeField(blank=True, null=True)
    state_locked_until = models.DateTimeField(null=True, blank=True, db_index=True)

    local = models.BooleanField(db_index=True)
    users = models.ManyToManyField(
        "takahe.User",
        related_name="identities",
        blank=True,
    )

    username = models.CharField(max_length=500, blank=True, null=True)
    # Must be a display domain if present
    domain = models.ForeignKey(
        Domain,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="identities",
    )

    name = models.CharField(
        max_length=500, blank=True, null=True, verbose_name=_("Display Name")
    )
    summary = models.TextField(blank=True, null=True, verbose_name=_("Bio"))
    manually_approves_followers = models.BooleanField(
        default=False, verbose_name=_("Manually approve new followers")
    )
    discoverable = models.BooleanField(
        default=True,
        verbose_name=_("Include profile and posts in search and discovery"),
    )

    profile_uri = models.CharField(max_length=500, blank=True, null=True)
    inbox_uri = models.CharField(max_length=500, blank=True, null=True)
    shared_inbox_uri = models.CharField(max_length=500, blank=True, null=True)
    outbox_uri = models.CharField(max_length=500, blank=True, null=True)
    icon_uri = models.CharField(max_length=500, blank=True, null=True)
    image_uri = models.CharField(max_length=500, blank=True, null=True)
    followers_uri = models.CharField(max_length=500, blank=True, null=True)
    following_uri = models.CharField(max_length=500, blank=True, null=True)
    featured_collection_uri = models.CharField(max_length=500, blank=True, null=True)
    actor_type = models.CharField(max_length=100, default="person")

    icon = models.ImageField(
        upload_to=partial(upload_namer, "profile_images"),
        blank=True,
        null=True,
        verbose_name=_("Profile picture"),
        storage=upload_store,
    )
    image = models.ImageField(
        upload_to=partial(upload_namer, "background_images"),
        blank=True,
        null=True,
        verbose_name=_("Header picture"),
        storage=upload_store,
    )

    # Should be a list of {"name":..., "value":...} dicts
    metadata = models.JSONField(blank=True, null=True)

    # Should be a list of object URIs (we don't want a full M2M here)
    pinned = models.JSONField(blank=True, null=True)

    # Admin-only moderation fields
    sensitive = models.BooleanField(default=False)
    restriction = models.IntegerField(
        choices=Restriction.choices, default=Restriction.none, db_index=True
    )
    admin_notes = models.TextField(null=True, blank=True)

    private_key = models.TextField(null=True, blank=True)
    public_key = models.TextField(null=True, blank=True)
    public_key_id = models.TextField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    fetched = models.DateTimeField(null=True, blank=True)
    deleted = models.DateTimeField(null=True, blank=True)

    # objects = IdentityManager()

    ### Model attributes ###

    class Meta:
        # managed = False
        db_table = "users_identity"
        verbose_name_plural = "identities"
        unique_together = [("username", "domain")]
        indexes: list = []  # We need this so Stator can add its own

    class urls(urlman.Urls):
        view = "/@{self.username}@{self.domain_id}/"
        replies = "{view}replies/"
        settings = "{view}settings/"
        action = "{view}action/"
        followers = "{view}followers/"
        following = "{view}following/"
        search = "{view}search/"
        activate = "{view}activate/"
        admin = "/admin/identities/"
        admin_edit = "{admin}{self.pk}/"
        djadmin_edit = "/djadmin/users/identity/{self.id}/change/"

        def get_scheme(self, url):  # pyright: ignore
            return "https"

        def get_hostname(self, url):
            return self.instance.domain.uri_domain

    def __str__(self):
        if self.username and self.domain:
            return self.handle
        return self.actor_uri

    def absolute_profile_uri(self):
        """
        Returns a profile URI that is always absolute, for sending out to
        other servers.
        """
        if self.local:
            return f"https://{self.domain.uri_domain}/@{self.username}/"
        else:
            return self.profile_uri

    @property
    def handle(self):
        if self.username is None:
            return "(unknown user)"
        if self.domain_id:
            return f"{self.username}@{self.domain_id}"
        return f"{self.username}@(unknown server)"

    @property
    def url(self):
        return (
            f"/users/{self.username}/"
            if self.local
            else f"/users/@{self.username}@{self.domain_id}/"
        )

    @property
    def user_pk(self):
        user = self.users.first()
        return user.pk if user else None

    @classmethod
    def fetch_webfinger_url(cls, domain: str) -> str:
        """
        Given a domain (hostname), returns the correct webfinger URL to use
        based on probing host-meta.
        """
        with httpx.Client(
            timeout=settings.TAKAHE_REMOTE_TIMEOUT,
            headers={"User-Agent": settings.TAKAHE_USER_AGENT},
        ) as client:
            try:
                response = client.get(
                    f"https://{domain}/.well-known/host-meta",
                    follow_redirects=True,
                    headers={"Accept": "application/xml"},
                )

                # In the case of anything other than a success, we'll still try
                # hitting the webfinger URL on the domain we were given to handle
                # incorrectly setup servers.
                if response.status_code == 200 and response.content.strip():
                    tree = etree.fromstring(response.content)
                    template = tree.xpath(
                        "string(.//*[local-name() = 'Link' and @rel='lrdd' and (not(@type) or @type='application/jrd+json')]/@template)"
                    )
                    if template:
                        return template  # type: ignore
            except (httpx.RequestError, etree.ParseError):
                pass

        return f"https://{domain}/.well-known/webfinger?resource={{uri}}"

    @classmethod
    def fetch_webfinger(cls, handle: str) -> tuple[str | None, str | None]:
        """
        Given a username@domain handle, returns a tuple of
        (actor uri, canonical handle) or None, None if it does not resolve.
        """
        domain = handle.split("@")[1].lower()
        try:
            webfinger_url = cls.fetch_webfinger_url(domain)
        except ssl.SSLCertVerificationError:
            return None, None

        # Go make a Webfinger request
        with httpx.Client(
            timeout=settings.TAKAHE_REMOTE_TIMEOUT,
            headers={"User-Agent": settings.TAKAHE_USER_AGENT},
        ) as client:
            try:
                response = client.get(
                    webfinger_url.format(uri=f"acct:{handle}"),
                    follow_redirects=True,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            except (httpx.HTTPError, ssl.SSLCertVerificationError) as ex:
                response = getattr(ex, "response", None)
                if (
                    response
                    and response.status_code < 500
                    and response.status_code not in [400, 401, 403, 404, 406, 410]
                ):
                    raise ValueError(
                        f"Client error fetching webfinger: {response.status_code}",
                        response.content,
                    )
                return None, None

        try:
            data = response.json()
        except ValueError:
            # Some servers return these with a 200 status code!
            if b"not found" in response.content.lower():
                return None, None
            raise ValueError(
                "JSON parse error fetching webfinger",
                response.content,
            )
        try:
            if data["subject"].startswith("acct:"):
                data["subject"] = data["subject"][5:]
            for link in data["links"]:
                if (
                    link.get("type") == "application/activity+json"
                    and link.get("rel") == "self"
                ):
                    return link["href"], data["subject"]
        except KeyError:
            # Server returning wrong payload structure
            pass
        return None, None

    @classmethod
    def by_username_and_domain(
        cls,
        username: str,
        domain: str | Domain,
        fetch: bool = False,
        local: bool = False,
    ):
        """
        Get an Identity by username and domain.

        When fetch is True, a failed lookup will do a webfinger lookup to attempt to do
        a lookup by actor_uri, creating an Identity record if one does not exist. When
        local is True, lookups will be restricted to local domains.

        If domain is a Domain, domain.local is used instead of passsed local.

        """
        if username.startswith("@"):
            raise ValueError("Username must not start with @")

        domain_instance = None

        if isinstance(domain, Domain):
            domain_instance = domain
            local = domain.local
            domain = domain.domain
        else:
            domain = domain.lower()
        try:
            if local:
                return cls.objects.get(
                    username__iexact=username,
                    domain_id=domain,
                    local=True,
                )
            else:
                return cls.objects.get(
                    username__iexact=username,
                    domain_id=domain,
                )
        except cls.DoesNotExist:
            if fetch and not local:
                actor_uri, handle = cls.fetch_webfinger(f"{username}@{domain}")
                if handle is None:
                    return None
                # See if this actually does match an existing actor
                try:
                    return cls.objects.get(actor_uri=actor_uri)
                except cls.DoesNotExist:
                    pass
                # OK, make one
                username, domain = handle.split("@")
                if not domain_instance:
                    domain_instance = Domain.get_remote_domain(domain)
                return cls.objects.create(
                    actor_uri=actor_uri,
                    username=username,
                    domain_id=domain_instance,
                    local=False,
                )
            return None

    def generate_keypair(self):
        if not self.local:
            raise ValueError("Cannot generate keypair for remote user")
        self.private_key, self.public_key = RsaKeys.generate_keypair()
        self.public_key_id = self.actor_uri + "#main-key"
        self.save()

    def ensure_uris(self):
        """
        Ensures that local identities have all the URIs populated on their fields
        (this lets us add new ones easily)
        """
        if self.local:
            self.inbox_uri = self.actor_uri + "inbox/"
            self.outbox_uri = self.actor_uri + "outbox/"
            self.featured_collection_uri = self.actor_uri + "collections/featured/"
            self.followers_uri = self.actor_uri + "followers/"
            self.following_uri = self.actor_uri + "following/"
            self.shared_inbox_uri = f"https://{self.domain.uri_domain}/inbox/"
            self.save()


class Follow(models.Model):
    """
    When one user (the source) follows other (the target)
    """

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_follow)

    source = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="outbound_follows",
    )
    target = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="inbound_follows",
    )

    boosts = models.BooleanField(
        default=True, help_text="Also follow boosts from this user"
    )

    uri = models.CharField(blank=True, null=True, max_length=500)
    note = models.TextField(blank=True, null=True)

    # state = StateField(FollowStates)
    state = models.CharField(max_length=100, default="unrequested")
    state_changed = models.DateTimeField(auto_now_add=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "users_follow"
        unique_together = [("source", "target")]
        indexes: list = []  # We need this so Stator can add its own

    def __str__(self):
        return f"#{self.id}: {self.source} → {self.target}"


class PostQuerySet(models.QuerySet):
    def not_hidden(self):
        query = self.exclude(state__in=["deleted", "deleted_fanned_out"])
        return query

    def public(self, include_replies: bool = False):
        query = self.filter(
            visibility__in=[
                Post.Visibilities.public,
                Post.Visibilities.local_only,
            ],
        )
        if not include_replies:
            return query.filter(in_reply_to__isnull=True)
        return query

    def local_public(self, include_replies: bool = False):
        query = self.filter(
            visibility__in=[
                Post.Visibilities.public,
                Post.Visibilities.local_only,
            ],
            local=True,
        )
        if not include_replies:
            return query.filter(in_reply_to__isnull=True)
        return query

    def unlisted(self, include_replies: bool = False):
        query = self.filter(
            visibility__in=[
                Post.Visibilities.public,
                Post.Visibilities.local_only,
                Post.Visibilities.unlisted,
            ],
        )
        if not include_replies:
            return query.filter(in_reply_to__isnull=True)
        return query

    def visible_to(self, identity: Identity | None, include_replies: bool = False):
        if identity is None:
            return self.unlisted(include_replies=include_replies)
        query = self.filter(
            models.Q(
                visibility__in=[
                    Post.Visibilities.public,
                    Post.Visibilities.local_only,
                    Post.Visibilities.unlisted,
                ]
            )
            | models.Q(
                visibility=Post.Visibilities.followers,
                author__inbound_follows__source=identity,
            )
            | models.Q(
                mentions=identity,
            )
            | models.Q(author=identity)
        ).distinct()
        if not include_replies:
            return query.filter(in_reply_to__isnull=True)
        return query

    # def tagged_with(self, hashtag: str | Hashtag):
    #     if isinstance(hashtag, str):
    #         tag_q = models.Q(hashtags__contains=hashtag)
    #     else:
    #         tag_q = models.Q(hashtags__contains=hashtag.hashtag)
    #         if hashtag.aliases:
    #             for alias in hashtag.aliases:
    #                 tag_q |= models.Q(hashtags__contains=alias)
    #     return self.filter(tag_q)


class PostManager(models.Manager):
    def get_queryset(self):
        return PostQuerySet(self.model, using=self._db)

    def not_hidden(self):
        return self.get_queryset().not_hidden()

    def public(self, include_replies: bool = False):
        return self.get_queryset().public(include_replies=include_replies)

    def local_public(self, include_replies: bool = False):
        return self.get_queryset().local_public(include_replies=include_replies)

    def unlisted(self, include_replies: bool = False):
        return self.get_queryset().unlisted(include_replies=include_replies)

    # def tagged_with(self, hashtag: str | Hashtag):
    #     return self.get_queryset().tagged_with(hashtag=hashtag)


class Post(models.Model):
    """
    A post (status, toot) that is either local or remote.
    """

    if TYPE_CHECKING:
        interactions: "models.QuerySet[PostInteraction]"
        attachments: "models.QuerySet[PostAttachment]"

    class Visibilities(models.IntegerChoices):
        public = 0
        local_only = 4
        unlisted = 1
        followers = 2
        mentioned = 3

    class Types(models.TextChoices):
        article = "Article"
        audio = "Audio"
        event = "Event"
        image = "Image"
        note = "Note"
        page = "Page"
        question = "Question"
        video = "Video"

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_post)

    # The author (attributedTo) of the post
    author = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="posts",
    )

    # The state the post is in
    # state = StateField(PostStates)
    state = models.CharField(max_length=100, default="new")
    state_changed = models.DateTimeField(auto_now_add=True)

    # If it is our post or not
    local = models.BooleanField()

    # The canonical object ID
    object_uri = models.CharField(max_length=2048, blank=True, null=True, unique=True)

    # Who should be able to see this Post
    visibility = models.IntegerField(
        choices=Visibilities.choices,
        default=Visibilities.public,
    )

    # The main (HTML) content
    content = models.TextField()

    type = models.CharField(
        max_length=20,
        choices=Types.choices,
        default=Types.note,
    )
    type_data = models.JSONField(
        blank=True,
        null=True,  # , encoder=PostTypeDataEncoder, decoder=PostTypeDataDecoder
    )

    # If the contents of the post are sensitive, and the summary (content
    # warning) to show if it is
    sensitive = models.BooleanField(default=False)
    summary = models.TextField(blank=True, null=True)

    # The public, web URL of this Post on the original server
    url = models.CharField(max_length=2048, blank=True, null=True)

    # The Post it is replying to as an AP ID URI
    # (as otherwise we'd have to pull entire threads to use IDs)
    in_reply_to = models.CharField(max_length=500, blank=True, null=True, db_index=True)

    # The identities the post is directly to (who can see it if not public)
    to = models.ManyToManyField(
        "takahe.Identity",
        related_name="posts_to",
        blank=True,
    )

    # The identities mentioned in the post
    mentions = models.ManyToManyField(
        "takahe.Identity",
        related_name="posts_mentioning",
        blank=True,
    )

    # Hashtags in the post
    hashtags = models.JSONField(blank=True, null=True)

    emojis = models.ManyToManyField(
        "takahe.Emoji",
        related_name="posts_using_emoji",
        blank=True,
    )

    # Like/Boost/etc counts
    stats = models.JSONField(blank=True, null=True)

    # When the post was originally created (as opposed to when we received it)
    published = models.DateTimeField(default=timezone.now)

    # If the post has been edited after initial publication
    edited = models.DateTimeField(blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects: PostManager = PostManager()

    class Meta:
        # managed = False
        db_table = "activities_post"

    class urls(urlman.Urls):
        view = "{self.author.urls.view}posts/{self.id}/"
        object_uri = "{self.author.actor_uri}posts/{self.id}/"
        action_like = "{view}like/"
        action_unlike = "{view}unlike/"
        action_boost = "{view}boost/"
        action_unboost = "{view}unboost/"
        action_bookmark = "{view}bookmark/"
        action_unbookmark = "{view}unbookmark/"
        action_delete = "{view}delete/"
        action_edit = "{view}edit/"
        action_report = "{view}report/"
        action_reply = "/compose/?reply_to={self.id}"
        admin_edit = "/djadmin/activities/post/{self.id}/change/"

        def get_scheme(self, url):  # pyright: ignore
            return "https"

        def get_hostname(self, url):
            return self.instance.author.domain.uri_domain

    def __str__(self):
        return f"{self.author} #{self.id}"

    def get_absolute_url(self):
        return self.urls.view

    def absolute_object_uri(self):
        """
        Returns an object URI that is always absolute, for sending out to
        other servers.
        """
        if self.local:
            return self.author.absolute_profile_uri() + f"posts/{self.id}/"
        else:
            return self.object_uri

    def in_reply_to_post(self) -> Optional["Post"]:
        """
        Returns the actual Post object we're replying to, if we can find it
        """
        if self.in_reply_to is None:
            return None
        return (
            Post.objects.filter(object_uri=self.in_reply_to)
            .select_related("author")
            .first()
        )

    def add_to_timeline(self, owner: Identity):
        """
        Creates a TimelineEvent for this post on owner's timeline
        """
        return TimelineEvent.objects.update_or_create(
            identity=owner,
            type=TimelineEvent.Types.post,
            subject_post=self,
            subject_identity=self.author,
            defaults={
                "published": self.published,
            },
        )[0]

    @classmethod
    def create_local(
        cls,
        author: Identity,
        raw_prepend_content: str,
        content: str,
        summary: str | None = None,
        sensitive: bool = False,
        visibility: int = Visibilities.public,
        reply_to: Optional["Post"] = None,
        attachments: list | None = None,
        type_data: dict | None = None,
        published: datetime.datetime | None = None,
    ) -> "Post":
        with transaction.atomic():
            # Find mentions in this post
            mentions = (
                set() if _migration_mode else cls.mentions_from_content(content, author)
            )
            if reply_to:
                mentions.add(reply_to.author)
                # Maintain local-only for replies
                if reply_to.visibility == reply_to.Visibilities.local_only:
                    visibility = reply_to.Visibilities.local_only
            # Find emoji in this post
            emojis = [] if _migration_mode else Emoji.emojis_from_content(content, None)
            # Strip all unwanted HTML and apply linebreaks filter, grabbing hashtags on the way
            parser = FediverseHtmlParser(linebreaks_filter(content), find_hashtags=True)
            content = parser.html.replace("<p>", "<p>" + raw_prepend_content, 1)
            hashtags = (
                sorted([tag[: Hashtag.MAXIMUM_LENGTH] for tag in parser.hashtags])
                or None
            )
            post_obj = {
                "author": author,
                "content": content,
                "summary": summary or None,
                "sensitive": bool(summary) or sensitive,
                "local": True,
                "visibility": visibility,
                "hashtags": hashtags,
                "in_reply_to": reply_to.object_uri if reply_to else None,
            }
            if published:
                _delta = timezone.now() - published
                if _delta > datetime.timedelta(0):
                    post_obj["published"] = published
                    if _delta > datetime.timedelta(days=settings.FANOUT_LIMIT_DAYS):
                        post_obj["id"] = Snowflake.generate_post_at(
                            published.timestamp()
                        )
                        post_obj["state"] = "fanned_out"  # add post quietly if it's old
            with transaction.atomic(using="takahe"):
                # Make the Post object
                post = cls.objects.create(**post_obj)

                if _migration_mode:
                    post.state = "fanned_out"
                else:
                    post.mentions.set(mentions)
                    post.emojis.set(emojis)
                post.object_uri = post.urls.object_uri
                post.url = post.absolute_object_uri()
                if attachments:
                    post.attachments.set(attachments)
                # if question: # FIXME
                #     post.type = question["type"]
                #     post.type_data = PostTypeData(__root__=question).__root__
                if type_data:
                    post.type_data = type_data
                post.save()
            # Recalculate parent stats for replies
            if reply_to:
                reply_to.calculate_stats()
            if post.state == "fanned_out" and not _migration_mode:
                # add post to auther's timeline directly if it's old
                post.add_to_timeline(author)
        return post

    def edit_local(
        self,
        raw_prepend_content: str,
        content: str,
        summary: str | None = None,
        sensitive: bool | None = None,
        visibility: int = Visibilities.public,
        attachments: list | None = None,
        attachment_attributes: list | None = None,
        type_data: dict | None = None,
        published: datetime.datetime | None = None,
    ):
        with transaction.atomic():
            # Strip all HTML and apply linebreaks filter
            parser = FediverseHtmlParser(linebreaks_filter(content), find_hashtags=True)
            self.content = parser.html.replace("<p>", "<p>" + raw_prepend_content, 1)
            self.hashtags = (
                sorted([tag[: Hashtag.MAXIMUM_LENGTH] for tag in parser.hashtags])
                or None
            )
            self.summary = summary or None
            self.sensitive = bool(summary) if sensitive is None else sensitive
            self.visibility = visibility
            self.edited = timezone.now()
            self.mentions.set(self.mentions_from_content(content, self.author))
            self.emojis.set(Emoji.emojis_from_content(content, None))
            if attachments is not None:
                self.attachments.set(attachments or [])  # type: ignore
            if type_data:
                self.type_data = type_data
            self.save()

            for attrs in attachment_attributes or []:
                attachment = next(
                    (a for a in attachments or [] if str(a.id) == attrs.id), None
                )
                if attachment is None:
                    continue
                attachment.name = attrs.description
                attachment.save()

            self.state = "edited"
            self.state_changed = timezone.now()
            self.state_next_attempt = None
            self.state_locked_until = None
            if _migration_mode:  # NeoDB: disable fanout during migration
                self.state = "edited_fanned_out"
            self.save()

    @classmethod
    def mentions_from_content(cls, content, author) -> set[Identity]:
        mention_hits = FediverseHtmlParser(content, find_mentions=True).mentions
        mentions = set()
        for handle in mention_hits:
            handle = handle.lower()
            if "@" in handle:
                username, domain = handle.split("@", 1)
                local = False
            else:
                username = handle
                domain = author.domain_id
                local = author.local
            identity = Identity.by_username_and_domain(
                username=username, domain=domain, fetch=True, local=local
            )
            if identity is not None:
                mentions.add(identity)
        return mentions

    def calculate_stats(self, save=True):
        """
        Recalculates our stats dict
        """
        from .models import PostInteraction

        self.stats = {
            "likes": self.interactions.filter(
                type=PostInteraction.Types.like,
                state__in=["new", "fanned_out"],
            ).count(),
            "boosts": self.interactions.filter(
                type=PostInteraction.Types.boost,
                state__in=["new", "fanned_out"],
            ).count(),
            "replies": Post.objects.filter(in_reply_to=self.object_uri).count(),
        }
        if save:
            self.save()

    @property
    def safe_content_local(self):
        return ContentRenderer(local=True).render_post(self.content, self)


class FanOut(models.Model):
    """
    An activity that needs to get to an inbox somewhere.
    """

    class Meta:
        # managed = False
        db_table = "activities_fanout"

    class Types(models.TextChoices):
        post = "post"
        post_edited = "post_edited"
        post_deleted = "post_deleted"
        interaction = "interaction"
        undo_interaction = "undo_interaction"
        identity_edited = "identity_edited"
        identity_deleted = "identity_deleted"
        identity_created = "identity_created"
        identity_moved = "identity_moved"

    state = models.CharField(max_length=100, default="outdated")
    state_changed = models.DateTimeField(auto_now_add=True)

    # The user this event is targeted at
    # We always need this, but if there is a shared inbox URL on the user
    # we'll deliver to that and won't have fanouts for anyone else with the
    # same one.
    identity = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="fan_outs",
    )

    # What type of activity it is
    type = models.CharField(max_length=100, choices=Types.choices)

    # Links to the appropriate objects
    subject_post = models.ForeignKey(
        "takahe.Post",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="fan_outs",
    )
    subject_post_interaction = models.ForeignKey(
        "takahe.PostInteraction",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="fan_outs",
    )
    subject_identity = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="subject_fan_outs",
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)


class PostAttachment(models.Model):
    """
    An attachment to a Post. Could be an image, a video, etc.
    """

    post = models.ForeignKey(
        "takahe.post",
        on_delete=models.CASCADE,
        related_name="attachments",
        blank=True,
        null=True,
    )
    author = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="attachments",
        blank=True,
        null=True,
    )

    # state = StateField(graph=PostAttachmentStates)
    state = models.CharField(max_length=100, default="new")
    state_changed = models.DateTimeField(auto_now_add=True)

    mimetype = models.CharField(max_length=200)

    # Files may not be populated if it's remote and not cached on our side yet
    file = models.FileField(
        upload_to=partial(upload_namer, "attachments"),
        null=True,
        blank=True,
        storage=upload_store,
    )
    thumbnail = models.ImageField(
        upload_to=partial(upload_namer, "attachment_thumbnails"),
        null=True,
        blank=True,
        storage=upload_store,
    )

    remote_url = models.CharField(max_length=500, null=True, blank=True)

    # This is the description for images, at least
    name = models.TextField(null=True, blank=True)

    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    focal_x = models.FloatField(null=True, blank=True)
    focal_y = models.FloatField(null=True, blank=True)
    blurhash = models.TextField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "activities_postattachment"

    def is_image(self):
        return self.mimetype in [
            "image/apng",
            "image/avif",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
        ]

    def is_video(self):
        return self.mimetype in [
            "video/mp4",
            "video/ogg",
            "video/webm",
        ]

    def thumbnail_url(self) -> RelativeAbsoluteUrl:
        if self.thumbnail:
            return RelativeAbsoluteUrl(self.thumbnail.url)
        elif self.file:
            return RelativeAbsoluteUrl(self.file.url)
        else:
            return ProxyAbsoluteUrl(
                f"/proxy/post_attachment/{self.pk}/",
                remote_url=self.remote_url,
            )

    def full_url(self):
        if self.file:
            return RelativeAbsoluteUrl(self.file.url)
        if self.is_image():
            return ProxyAbsoluteUrl(
                f"/proxy/post_attachment/{self.pk}/",
                remote_url=self.remote_url,
            )
        return RelativeAbsoluteUrl(self.remote_url)

    @property
    def file_display_name(self):
        if self.remote_url:
            return self.remote_url.rsplit("/", 1)[-1]
        if self.file:
            return self.file.name.rsplit("/", 1)[-1]
        return f"attachment ({self.mimetype})"


class EmojiQuerySet(models.QuerySet):
    def usable(self, domain: Domain | None = None):
        """
        Returns all usable emoji, optionally filtering by domain too.
        """
        visible_q = models.Q(local=True) | models.Q(public=True)
        if True:  # Config.system.emoji_unreviewed_are_public:
            visible_q |= models.Q(public__isnull=True)
        qs = self.filter(visible_q)

        if domain:
            if not domain.local:
                qs = qs.filter(domain=domain)

        return qs


class EmojiManager(models.Manager):
    def get_queryset(self):
        return EmojiQuerySet(self.model, using=self._db)

    def usable(self, domain: Domain | None = None):
        return self.get_queryset().usable(domain)


class Emoji(models.Model):
    class Meta:
        # managed = False
        db_table = "activities_emoji"

    # Normalized Emoji without the ':'
    shortcode = models.SlugField(max_length=100, db_index=True)

    domain = models.ForeignKey(
        "takahe.Domain", null=True, blank=True, on_delete=models.CASCADE
    )
    local = models.BooleanField(default=True)

    # Should this be shown in the public UI?
    public = models.BooleanField(null=True)

    object_uri = models.CharField(max_length=500, blank=True, null=True, unique=True)

    mimetype = models.CharField(max_length=200)

    # Files may not be populated if it's remote and not cached on our side yet
    file = models.ImageField(
        # upload_to=partial(upload_emoji_namer, "emoji"),
        null=True,
        blank=True,
    )

    # A link to the custom emoji
    remote_url = models.CharField(max_length=500, blank=True, null=True)

    # Used for sorting custom emoji in the picker
    category = models.CharField(max_length=100, blank=True, null=True)

    # State of this Emoji
    # state = StateField(EmojiStates)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    objects = EmojiManager()

    @classmethod
    def emojis_from_content(cls, content: str, domain: Domain | None) -> list["Emoji"]:
        """
        Return a parsed and sanitized of emoji found in content without
        the surrounding ':'.
        """
        emoji_hits = FediverseHtmlParser(
            content, find_emojis=True, emoji_domain=domain
        ).emojis
        emojis = sorted({emoji for emoji in emoji_hits})
        q = models.Q(local=True) | models.Q(public=True) | models.Q(public__isnull=True)
        if domain and not domain.local:
            q = q & models.Q(domain=domain)
        return list(
            cls.objects.filter(local=(domain is None) or domain.local)
            .filter(q)
            .filter(shortcode__in=emojis)
        )

    @classmethod
    @cached(cache=TTLCache(maxsize=1000, ttl=60))
    def get_by_domain(cls, shortcode, domain: Domain | None) -> "Emoji | None":
        """
        Given an emoji shortcode and optional domain, looks up the single
        emoji and returns it. Raises Emoji.DoesNotExist if there isn't one.
        """
        try:
            if domain is None or domain.local:
                return cls.objects.get(local=True, shortcode=shortcode)
            else:
                return cls.objects.get(domain=domain, shortcode=shortcode)
        except Emoji.DoesNotExist:
            return None

    @property
    def fullcode(self):
        return f":{self.shortcode}:"

    @property
    def is_usable(self) -> bool:
        """
        Return True if this Emoji is usable.
        """
        return self.public or self.public is None

    def full_url(self, always_show=False) -> RelativeAbsoluteUrl:
        if self.is_usable or always_show:
            if self.file:
                return AutoAbsoluteUrl(settings.TAKAHE_MEDIA_URL + self.file.name)
                # return AutoAbsoluteUrl(self.file.url)
            elif self.remote_url:
                return ProxyAbsoluteUrl(
                    f"/proxy/emoji/{self.pk}/",
                    remote_url=self.remote_url,
                )
        return StaticAbsoluteUrl("img/blank-emoji-128.png")

    def as_html(self):
        if self.is_usable:
            return mark_safe(
                f'<img src="{self.full_url().relative}" class="emoji" alt="Emoji {self.shortcode}">'
            )
        return self.fullcode


class HashtagQuerySet(models.QuerySet):
    def public(self):
        public_q = models.Q(public=True)
        if True:  # Config.system.hashtag_unreviewed_are_public:
            public_q |= models.Q(public__isnull=True)
        return self.filter(public_q)

    def hashtag_or_alias(self, hashtag: str):
        return self.filter(
            models.Q(hashtag=hashtag) | models.Q(aliases__contains=hashtag)
        )


class HashtagManager(models.Manager):
    def get_queryset(self):
        return HashtagQuerySet(self.model, using=self._db)

    def public(self):
        return self.get_queryset().public()

    def hashtag_or_alias(self, hashtag: str):
        return self.get_queryset().hashtag_or_alias(hashtag)


class Hashtag(models.Model):
    class Meta:
        # managed = False
        db_table = "activities_hashtag"

    MAXIMUM_LENGTH = 100

    # Normalized hashtag without the '#'
    hashtag = models.SlugField(primary_key=True, max_length=100)

    # Friendly display override
    name_override = models.CharField(max_length=100, null=True, blank=True)

    # Should this be shown in the public UI?
    public = models.BooleanField(null=True)

    # State of this Hashtag
    # state = StateField(HashtagStates)
    state = models.CharField(max_length=100, default="outdated")
    state_changed = models.DateTimeField(auto_now_add=True)

    # Metrics for this Hashtag
    stats = models.JSONField(null=True, blank=True)
    # Timestamp of last time the stats were updated
    stats_updated = models.DateTimeField(null=True, blank=True)

    # List of other hashtags that are considered similar
    aliases = models.JSONField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    objects = HashtagManager()

    class urls(urlman.Urls):
        view = "/tags/{self.hashtag}/"
        follow = "/tags/{self.hashtag}/follow/"
        unfollow = "/tags/{self.hashtag}/unfollow/"
        admin = "/admin/hashtags/"
        admin_edit = "{admin}{self.hashtag}/"
        admin_enable = "{admin_edit}enable/"
        admin_disable = "{admin_edit}disable/"
        timeline = "/tags/{self.hashtag}/"

    hashtag_regex = re.compile(r"\B#([a-zA-Z0-9(_)]+\b)(?!;)")

    def save(self, *args, **kwargs):
        self.hashtag = self.hashtag.lstrip("#")
        if self.name_override:
            self.name_override = self.name_override.lstrip("#")
        return super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.name_override or self.hashtag

    def __str__(self):
        return self.display_name

    def usage_months(self, num: int = 12) -> dict[date, int]:
        """
        Return the most recent num months of stats
        """
        if not self.stats:
            return {}
        results = {}
        for key, val in self.stats.items():
            parts = key.split("-")
            if len(parts) == 2:
                year = int(parts[0])
                month = int(parts[1])
                results[date(year, month, 1)] = val
        return dict(sorted(results.items(), reverse=True)[:num])

    def usage_days(self, num: int = 7) -> dict[date, int]:
        """
        Return the most recent num days of stats
        """
        if not self.stats:
            return {}
        results = {}
        for key, val in self.stats.items():
            parts = key.split("-")
            if len(parts) == 3:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                results[date(year, month, day)] = val
        return dict(sorted(results.items(), reverse=True)[:num])


class PostInteraction(models.Model):
    """
    Handles both boosts and likes
    """

    class Types(models.TextChoices):
        like = "like"
        boost = "boost"
        vote = "vote"
        pin = "pin"

    id = models.BigIntegerField(
        primary_key=True,
        default=Snowflake.generate_post_interaction,
    )

    # The state the boost is in
    # state = StateField(PostInteractionStates)
    state = models.CharField(max_length=100, default="new")
    state_changed = models.DateTimeField(auto_now_add=True)

    # The canonical object ID
    object_uri = models.CharField(max_length=500, blank=True, null=True, unique=True)

    # What type of interaction it is
    type = models.CharField(max_length=100, choices=Types.choices)

    # The user who boosted/liked/etc.
    identity = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="interactions",
    )

    # The post that was boosted/liked/etc
    post = models.ForeignKey(
        "takahe.Post",
        on_delete=models.CASCADE,
        related_name="interactions",
    )

    # Used to store any interaction extra text value like the vote
    # in the question/poll case
    value = models.CharField(max_length=50, blank=True, null=True)

    # When the activity was originally created (as opposed to when we received it)
    # Mastodon only seems to send this for boosts, not likes
    published = models.DateTimeField(default=timezone.now)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "activities_postinteraction"


class Block(models.Model):
    """
    When one user (the source) mutes or blocks another (the target)
    """

    # state = StateField(BlockStates)
    state = models.CharField(max_length=100, default="new")
    state_changed = models.DateTimeField(auto_now_add=True)

    source = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="outbound_blocks",
    )

    target = models.ForeignKey(
        "takahe.Identity",
        on_delete=models.CASCADE,
        related_name="inbound_blocks",
    )

    uri = models.CharField(blank=True, null=True, max_length=500)

    # If it is a mute, we will stop delivering any activities from target to
    # source, but we will still deliver activities from source to target.
    # A full block (mute=False) stops activities both ways.
    mute = models.BooleanField()
    include_notifications = models.BooleanField(default=False)

    expires = models.DateTimeField(blank=True, null=True)
    note = models.TextField(blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "users_block"

    def __str__(self):
        return f"#{self.pk}: {self.source} blocks {self.target}"

    ### Alternate fetchers/constructors ###

    @classmethod
    def maybe_get(
        cls, source, target, mute=False, require_active=False
    ) -> Optional["Block"]:
        """
        Returns a Block if it exists between source and target
        """
        try:
            if require_active:
                return cls.objects.filter(
                    status__in=["new", "sent", "awaiting_expiry"]
                ).get(source=source, target=target, mute=mute)
            else:
                return cls.objects.get(source=source, target=target, mute=mute)
        except cls.DoesNotExist:
            return None

    @classmethod
    def create_local_block(cls, source, target) -> "Block":
        """
        Creates or updates a full Block from a local Identity to the target
        (which can be local or remote).
        """
        if not source.local:
            raise ValueError("You cannot block from a remote Identity")
        block = cls.maybe_get(source=source, target=target, mute=False)
        if block is not None:
            if block.state not in ["new", "sent", "awaiting_expiry"]:
                block.state = BlockStates.new  # type:ignore
            block.save()
        else:
            with transaction.atomic():
                block = cls.objects.create(
                    source=source,
                    target=target,
                    mute=False,
                )
                block.uri = source.actor_uri + f"block/{block.pk}/"
                block.save()
        return block

    @classmethod
    def create_local_mute(
        cls,
        source,
        target,
        duration=None,
        include_notifications=False,
    ) -> "Block":
        """
        Creates or updates a muting Block from a local Identity to the target
        (which can be local or remote).
        """
        if not source.local:
            raise ValueError("You cannot mute from a remote Identity")
        block = cls.maybe_get(source=source, target=target, mute=True)
        if block is not None:
            if block not in ["new", "sent", "awaiting_expiry"]:
                block.state = BlockStates.new  # type:ignore
            if duration:
                block.expires = timezone.now() + datetime.timedelta(seconds=duration)
            block.include_notifications = include_notifications
            block.save()
        else:
            with transaction.atomic():
                block = cls.objects.create(
                    source=source,
                    target=target,
                    mute=True,
                    include_notifications=include_notifications,
                    expires=(
                        timezone.now() + datetime.timedelta(seconds=duration)
                        if duration
                        else None
                    ),
                )
                block.uri = source.actor_uri + f"block/{block.pk}/"
                block.save()
        return block


class InboxMessage(models.Model):
    """
    an incoming inbox message that needs processing.

    Yes, this is kind of its own message queue built on the state graph system.
    It's fine. It'll scale up to a decent point.
    """

    message = models.JSONField()

    # state = StateField(InboxMessageStates)
    state = models.CharField(max_length=100, default="received")
    state_changed = models.DateTimeField(auto_now_add=True)

    class Meta:
        # managed = False
        db_table = "users_inboxmessage"

    @classmethod
    def create_internal(cls, payload):
        """
        Creates an internal action message
        """
        cls.objects.create(
            message={
                "type": "__internal__",
                "object": payload,
            }
        )


class TimelineEvent(models.Model):
    """
    Something that has happened to an identity that we want them to see on one
    or more timelines, like posts, likes and follows.
    """

    class Types(models.TextChoices):
        post = "post"
        boost = "boost"  # A boost from someone (post substitute)
        mentioned = "mentioned"
        liked = "liked"  # Someone liking one of our posts
        followed = "followed"
        follow_requested = "follow_requested"
        boosted = "boosted"  # Someone boosting one of our posts
        announcement = "announcement"  # Server announcement
        identity_created = "identity_created"  # New identity created

    # The user this event is for
    identity = models.ForeignKey(
        Identity,
        on_delete=models.CASCADE,
        related_name="timeline_events",
    )

    # What type of event it is
    type = models.CharField(max_length=100, choices=Types.choices)

    # The subject of the event (which is used depends on the type)
    subject_post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="timeline_events",
    )
    subject_post_interaction = models.ForeignKey(
        PostInteraction,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="timeline_events",
    )
    subject_identity = models.ForeignKey(
        Identity,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="timeline_events_about_us",
    )

    published = models.DateTimeField(default=timezone.now)
    seen = models.BooleanField(default=False)
    dismissed = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # This relies on a DB that can use left subsets of indexes
            models.Index(
                fields=["identity", "type", "subject_post", "subject_identity"]
            ),
            models.Index(fields=["identity", "type", "subject_identity"]),
            models.Index(fields=["identity", "created"]),
        ]
        # managed = False
        db_table = "activities_timelineevent"


class Config(models.Model):
    """
    A configuration setting for either the server or a specific user or identity.

    The possible options and their defaults are defined at the bottom of the file.
    """

    key = models.CharField(max_length=500)

    user = models.ForeignKey(
        User,
        blank=True,
        null=True,
        related_name="configs",
        on_delete=models.CASCADE,
    )

    identity = models.ForeignKey(
        Identity,
        blank=True,
        null=True,
        related_name="configs",
        on_delete=models.CASCADE,
    )

    domain = models.ForeignKey(
        Domain,
        blank=True,
        null=True,
        related_name="configs",
        on_delete=models.CASCADE,
    )

    json = models.JSONField(blank=True, null=True)
    image = models.ImageField(
        blank=True,
        null=True,
    )

    class Meta:
        # managed = False
        db_table = "core_config"
        unique_together = [
            ("key", "user", "identity", "domain"),
        ]


class Relay(models.Model):
    inbox_uri = models.CharField(max_length=500, unique=True)

    # state = StateField(RelayStates)
    state = models.CharField(max_length=100, default="new")
    state_changed = models.DateTimeField(auto_now_add=True)
    state_next_attempt = models.DateTimeField(blank=True, null=True)
    state_locked_until = models.DateTimeField(null=True, blank=True, db_index=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "users_relay"


class Announcement(models.Model):
    """
    A server-wide announcement that users all see and can dismiss.
    """

    text = models.TextField()

    published = models.BooleanField(
        default=False,
    )
    start = models.DateTimeField(
        null=True,
        blank=True,
    )
    end = models.DateTimeField(
        null=True,
        blank=True,
    )

    include_unauthenticated = models.BooleanField(default=False)

    # Note that this is against User, not Identity - it's one of the few places
    # where we want it to be per login.
    seen = models.ManyToManyField("User", blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        # managed = False
        db_table = "users_announcement"

    @property
    def html(self) -> str:
        from journal.models import render_md

        return mark_safe(render_md(self.text))
