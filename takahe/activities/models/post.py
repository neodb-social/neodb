import datetime
import html
import json
import logging
import mimetypes
import re
import ssl
from collections.abc import Iterable
from typing import Optional
from urllib.parse import urlparse

import httpx
import urlman
from core.exceptions import ActivityPubFormatError, ActorMismatchError
from core.html import ContentRenderer, FediverseHtmlParser
from core.json import json_from_response
from core.ld import (
    canonicalise,
    format_ld_date,
    get_language,
    get_list,
    get_value_or_map,
    parse_ld_date,
)
from core.signatures import LDSignature
from core.snowflake import Snowflake
from deepmerge import always_merger
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
from django.db.utils import IntegrityError
from django.template import loader
from django.template.defaultfilters import linebreaks_filter
from django.utils import timezone
from django.utils.html import strip_tags
from pyld.jsonld import JsonLdError
from stator.exceptions import TryAgainLater
from stator.models import State, StateField, StateGraph, StatorModel
from users.models.block import Block
from users.models.follow import FollowStates
from users.models.hashtags import HashtagFollow
from users.models.identity import Identity, IdentityStates
from users.models.inbox_message import InboxMessage
from users.models.relay import Relay
from users.models.system_actor import SystemActor

from activities.models.emoji import Emoji
from activities.models.fan_out import FanOut
from activities.models.hashtag import Hashtag
from activities.models.post_types import (
    PostTypeData,
    PostTypeDataDecoder,
    PostTypeDataEncoder,
    QuestionData,
    vote_value,
)
from activities.models.quote_authorization import QuoteAuthorization

logger = logging.getLogger(__name__)

# Post.quote_url column width; URLs longer than this are rejected to avoid
# Postgres truncation errors (BookWyrm Quotation objects, for example, send
# the HTML quotation text under the `quote` key).
_QUOTE_URI_MAX_LENGTH = 2048


def _ap_link(value, preferred_media_type: str | None = None) -> tuple[str | None, dict]:
    """Return one URL and its Link/Object metadata from an AS url value.

    ActivityStreams permits URL values to be URI strings, embedded Link
    objects, or arrays of either. Prefer a requested media type (normally
    text/html for a status permalink), then fall back to the first usable
    value. The metadata is returned as well so callers can retain dimensions
    and media type information for icons and attachments.
    """

    candidates: list[tuple[str, dict]] = []

    def collect(item, inherited: dict | None = None) -> None:
        if isinstance(item, str):
            candidates.append((item, inherited or {}))
            return
        if isinstance(item, list):
            for child in item:
                collect(child, inherited)
            return
        if not isinstance(item, dict):
            return

        # AS Link uses href; Object subclasses usually use url.
        href = item.get("href")
        if isinstance(href, str):
            candidates.append((href, item))
        nested_url = item.get("url")
        if nested_url is not None:
            collect(nested_url, item)
        if not isinstance(href, str) and nested_url is None:
            object_id = item.get("id")
            if isinstance(object_id, str):
                candidates.append((object_id, item))

    collect(value)
    if not candidates:
        return None, {}
    if preferred_media_type:
        for url, metadata in candidates:
            media_type = metadata.get("mediaType")
            if (
                isinstance(media_type, str)
                and media_type.split(";", 1)[0].lower() == preferred_media_type
            ):
                return url, metadata
    return candidates[0]


def _natural_language_value(data: dict, key: str) -> str:
    """Read an AS natural-language field, coalescing JSON-LD list values."""

    try:
        value = get_value_or_map(data, key, f"{key}Map")
    except ActivityPubFormatError:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return value if isinstance(value, str) else ""


def _converted_post_content(data: dict, object_url: str) -> str:
    """Convert a non-status AS object into Mastodon-compatible status HTML.

    Mastodon's converted-object path keeps the body, title, summary, and a
    link to the original object in the status content (rather than exposing
    these object types as a separate client-API entity).
    """

    content = _natural_language_value(data, "content")
    name = _natural_language_value(data, "name")
    summary = _natural_language_value(data, "summary")
    parts: list[str] = []
    if content:
        parts.append(content)
    if name:
        # AS name is plain natural-language text, not trusted markup.
        parts.append(f"<p>{html.escape(strip_tags(name))}</p>")
    if summary:
        parts.append(summary)
    parts.append(
        f'<p><a href="{html.escape(object_url)}">{html.escape(object_url)}</a></p>'
    )
    return "\n".join(parts)


def _positive_int(value) -> int | None:
    try:
        value = int(value)
    except TypeError, ValueError:
        return None
    return value if value >= 0 else None


def _is_quote_uri(value: str) -> bool:
    return (
        bool(value)
        and value.startswith(("https://", "http://"))
        and len(value) <= _QUOTE_URI_MAX_LENGTH
    )


def _attach_preview_card(post_pk: int, content: str) -> None:
    """
    Extracts the first URL from post HTML content, strips tracking params,
    and links the post to a PreviewCard (creating one if needed).
    If no URL is found, unlinks any existing card.
    Uses direct queryset updates to avoid interfering with Stator state saves.
    """
    from django.utils import timezone

    from activities.models.preview_card import PreviewCard

    # Extract first URL from HTML content
    matches = FediverseHtmlParser.URL_REGEX.findall(content or "")
    url = matches[0].lstrip("(") if matches else None

    if not url or not url.startswith(("http://", "https://")):
        Post.objects.filter(pk=post_pk).update(preview_card=None)
        return

    canonical_url = PreviewCard.strip_tracking_params(url)
    if not canonical_url.startswith(("http://", "https://")):
        Post.objects.filter(pk=post_pk).update(preview_card=None)
        return

    card, _ = PreviewCard.objects.get_or_create(url=canonical_url)
    PreviewCard.objects.filter(pk=card.pk).update(last_referenced_at=timezone.now())
    Post.objects.filter(pk=post_pk).update(preview_card_id=card.pk)


class PostStates(StateGraph):
    new = State(try_interval=300)
    fanned_out = State(externally_progressed=True)
    deleted = State(try_interval=300)
    deleted_fanned_out = State(delete_after=86400)

    edited = State(try_interval=300)
    edited_fanned_out = State(externally_progressed=True)
    # Open polls: local ones federate throttled tally Updates and close at
    # expiry; remote ones with local voters get end-of-poll notifications.
    question_open = State(try_interval=300, attempt_immediately=False)

    new.transitions_to(fanned_out)
    new.transitions_to(question_open)
    fanned_out.transitions_to(deleted_fanned_out)
    fanned_out.transitions_to(deleted)
    fanned_out.transitions_to(edited)
    fanned_out.transitions_to(question_open)

    deleted.transitions_to(deleted_fanned_out)
    edited.transitions_to(edited_fanned_out)
    edited.transitions_to(question_open)
    edited_fanned_out.transitions_to(edited)
    edited_fanned_out.transitions_to(deleted)
    edited_fanned_out.transitions_to(question_open)
    question_open.transitions_to(fanned_out)
    question_open.transitions_to(edited)
    question_open.transitions_to(deleted)
    question_open.transitions_to(deleted_fanned_out)

    @classmethod
    def targets_fan_out(cls, post: "Post", type_: str) -> None:
        # Fan out to each target in bulk to avoid one INSERT round-trip per
        # follower (state/created defaults are applied the same as create()).
        FanOut.objects.bulk_create(
            (
                FanOut(identity=follow, type=type_, subject_post=post)
                for follow in post.get_targets()
            ),
            batch_size=500,
        )
        cls.fan_out_to_relay(post, type_)

    @classmethod
    def fan_out_to_relay(cls, post: "Post", type_: str) -> None:
        if not post.local or post.visibility != Post.Visibilities.public:
            return
        relay_uris = Relay.active_inbox_uris()
        if not relay_uris:
            return
        obj = None
        match type_:
            case FanOut.Types.post:
                obj = canonicalise(post.to_create_ap())
            case FanOut.Types.post_edited:
                obj = canonicalise(post.to_update_ap())
            case FanOut.Types.post_deleted:
                obj = canonicalise(post.to_delete_ap())
        if not obj:
            return
        # Attach LD signature so relay recipients can verify the original author
        # independently of the relay's HTTP signature.
        obj["signature"] = LDSignature.create_signature(
            obj, post.author.private_key, post.author.public_key_id
        )
        for uri in relay_uris:
            try:
                post.author.signed_request(method="post", uri=uri, body=obj)
            except Exception as e:
                logger.warning(f"Error sending relay: {uri} {e}")

    @classmethod
    def handle_new(cls, instance: "Post"):
        """
        Creates all needed fan-out objects for a new Post.
        """
        # Only fan out if the post was published in the last day or it's local
        # (we don't want to fan out anything older that that which is remote)
        if instance.local or (timezone.now() - instance.published) < datetime.timedelta(
            days=settings.FANOUT_LIMIT_DAYS
        ):
            cls.targets_fan_out(instance, FanOut.Types.post)
        instance.ensure_hashtags()
        if instance.type not in instance.CONVERTED_TYPES:
            _attach_preview_card(instance.pk, instance.content)
        if cls.needs_question_tracking(instance):
            return cls.question_open
        return cls.fanned_out

    @classmethod
    def handle_fanned_out(cls, instance: "Post"):
        """
        For remote posts, sees if we can delete them every so often.
        """
        # Skip all of this if the horizon is zero
        if settings.SETUP.REMOTE_PRUNE_HORIZON <= 0:
            return
        # To be a candidate for deletion, a post must be remote and old enough
        if instance.local:
            return
        if instance.created > timezone.now() - datetime.timedelta(
            days=settings.SETUP.REMOTE_PRUNE_HORIZON
        ):
            return
        # It must have no local interactions
        if instance.interactions.filter(identity__local=True).exists():
            return
        # OK, delete it!
        instance.delete()
        return cls.deleted_fanned_out

    @classmethod
    def handle_deleted(cls, instance: "Post"):
        """
        When a post is deleted:
        - Remove all timeline events
        - Remove all bookmarks
        - Undo all interactions (so that remote follower of a local booster will unsee it from their timeline)
        - Fan out the deletion of local post (fanout of remote post deletion is not supported yet)
        - Update conversation's last_post if needed
        """
        from users.models import Bookmark

        from .post_interaction import PostInteraction, PostInteractionStates
        from .timeline_event import TimelineEvent

        TimelineEvent.objects.filter(subject_post=instance).delete()
        Bookmark.objects.filter(post=instance).delete()
        if instance.local:
            cls.targets_fan_out(instance, FanOut.Types.post_deleted)
        PostInteraction.transition_perform_queryset(
            PostInteraction.objects.filter(
                post=instance,
                state__in=PostInteractionStates.group_active(),
            ),
            PostInteractionStates.undone,
        )
        # Update conversation's last_post if this was the latest
        if instance.conversation_id:
            conv = instance.conversation
            if conv.last_post_id == instance.pk:
                next_post = (
                    Post.objects.filter(conversation=conv)
                    .exclude(pk=instance.pk)
                    .not_hidden()
                    .order_by("-id")
                    .first()
                )
                conv.last_post = next_post
                conv.save(update_fields=["last_post", "updated"])
        # Keep the parent's replies_count accurate after a soft delete.
        instance.recalculate_parent_stats()
        return cls.deleted_fanned_out

    @classmethod
    def handle_edited(cls, instance: "Post"):
        """
        Creates all needed fan-out objects for an edited Post.
        """
        cls.targets_fan_out(instance, FanOut.Types.post_edited)
        instance.ensure_hashtags()
        if instance.type not in instance.CONVERTED_TYPES:
            _attach_preview_card(instance.pk, instance.content)
        if cls.needs_question_tracking(instance):
            question = instance.type_data
            if instance.local and question.last_distributed_tally != question.tally:
                # The Update we just fanned out carries the current tallies
                question.last_distributed_tally = question.tally
                instance.save()
            return cls.question_open
        return cls.edited_fanned_out

    @classmethod
    def needs_question_tracking(cls, instance: "Post") -> bool:
        """
        Whether this post should sit in question_open: an unexpired poll
        that is either local or has local voters to notify at expiry.
        """
        from activities.models.post_interaction import PostInteraction

        if instance.type != Post.Types.question or not isinstance(
            instance.type_data, QuestionData
        ):
            return False
        if instance.type_data.is_expired:
            return False
        if instance.local:
            return True
        if not instance.type_data.effective_end_time:
            # A remote poll that never ends has no expiry to track
            return False
        return instance.interactions.filter(
            type=PostInteraction.Types.vote, identity__local=True
        ).exists()

    @classmethod
    def handle_question_open(cls, instance: "Post"):
        """
        Watches an open poll: federates throttled tally Updates for local
        polls, and at expiry sends the final Update (now carrying `closed`)
        and notifies the author and local voters.
        """
        from activities.models.timeline_event import TimelineEvent

        with transaction.atomic():
            # Lock the row so concurrent vote handling can't clobber the
            # type_data we are about to save
            post = Post.objects.select_for_update().get(pk=instance.pk)
            if post.type != Post.Types.question or not isinstance(
                post.type_data, QuestionData
            ):
                return cls.fanned_out
            question = post.type_data
            if not question.is_expired:
                if (
                    post.local
                    and not question.hide_totals
                    and question.last_distributed_tally != question.tally
                ):
                    cls.targets_fan_out(post, FanOut.Types.post_edited)
                    question.last_distributed_tally = question.tally
                    post.save()
                return None
            # The poll has ended
            if post.local:
                # Final Update reveals hidden totals and carries `closed`
                cls.targets_fan_out(post, FanOut.Types.post_edited)
                question.last_distributed_tally = question.tally
                post.save()
                TimelineEvent.add_poll_ended(post.author, post)
                for voter in post.question_local_voters():
                    TimelineEvent.add_poll_ended(voter, post)
                return cls.fanned_out
        # Remote poll: refresh final tallies outside the row lock
        if not instance.refresh_question_from_remote():
            logger.warning(
                "Could not refresh final poll tallies for %s",
                instance.object_uri,
            )
        for voter in instance.question_local_voters():
            TimelineEvent.add_poll_ended(voter, instance)
        return cls.fanned_out


class PostQuerySet(models.QuerySet):
    def not_hidden(self):
        query = self.exclude(
            state__in=[PostStates.deleted, PostStates.deleted_fanned_out]
        )
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

    def visible_to(
        self,
        identity: Identity | None,
        include_replies: bool = False,
        include_muted: bool = False,
    ):
        if identity is None:
            return self.unlisted(include_replies=include_replies)
        # It's way faster to check follows and mentioned in subselects and drop the
        # DISTINCT. Also has the advantage of no LEFT OUTER JOINs to mess up
        # aggregation counts (like for PostInteraction counts).
        followed_ids = identity.outbound_follows.active().values_list(
            "target_id", flat=True
        )
        query = self.annotate(
            mentioned=models.Exists(
                Post.mentions.through.objects.filter(
                    post_id=models.OuterRef("id"), identity=identity
                )
            )
        ).filter(
            models.Q(
                visibility__in=[
                    Post.Visibilities.public,
                    Post.Visibilities.local_only,
                    Post.Visibilities.unlisted,
                ]
            )
            | models.Q(
                visibility=Post.Visibilities.followers,
                author_id__in=followed_ids,
            )
            | models.Q(
                mentioned=True,
            )
            | models.Q(author=identity)
        )
        if not include_replies:
            query = query.filter(in_reply_to__isnull=True)
        if identity:
            params = {"source_id": identity.pk}
            if include_muted:
                params["mute"] = False
            rejecting_ids = list(
                Block.objects.active().filter(**params).values_list("target", flat=True)
            ) + list(
                Block.objects.active()
                .filter(
                    target_id=identity.pk,
                    mute=False,
                )
                .values_list("source", flat=True)
            )
            query = query.exclude(author_id__in=rejecting_ids)
        return query

    def tagged_with(self, hashtag: str | Hashtag):
        if isinstance(hashtag, str):
            tag_q = models.Q(hashtags__contains=hashtag)
        else:
            tag_q = models.Q(hashtags__contains=hashtag.hashtag)
            if hashtag.aliases:
                for alias in hashtag.aliases:
                    tag_q |= models.Q(hashtags__contains=alias)
        return self.filter(tag_q)


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

    def tagged_with(self, hashtag: str | Hashtag):
        return self.get_queryset().tagged_with(hashtag=hashtag)


class Post(StatorModel):
    """
    A post (status, toot) that is either local or remote.
    """

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

    # Mastodon calls these "converted" objects: they become ordinary statuses
    # for client/API purposes, while their complete AS object remains available
    # in type_data for lossless handling by Takahē and NeoDB.
    CONVERTED_TYPES = frozenset(
        {
            Types.page,
            Types.image,
            Types.audio,
            Types.video,
            Types.event,
        }
    )

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_post)

    # The author (attributedTo) of the post
    author = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="posts",
    )

    # The application used to create this post (if created via API)
    application = models.ForeignKey(
        "api.Application",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="posts",
    )

    # Preview card for the first URL in this post's content
    preview_card = models.ForeignKey(
        "activities.PreviewCard",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posts",
    )

    # The state the post is in
    state = StateField(PostStates)

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

    # The language of the content
    language = models.CharField(default="", blank=True)

    type = models.CharField(
        max_length=20,
        choices=Types.choices,
        default=Types.note,
    )
    type_data = models.JSONField(
        blank=True, null=True, encoder=PostTypeDataEncoder, decoder=PostTypeDataDecoder
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

    # The Post this quotes, as an AP URI (FEP-044f)
    quote_url = models.CharField(max_length=2048, blank=True, null=True, db_index=True)

    # The conversation this post belongs to (only for direct messages)
    conversation = models.ForeignKey(
        "activities.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
    )

    # The identities the post is directly to (who can see it if not public)
    to = models.ManyToManyField(
        "users.Identity",
        related_name="posts_to",
        blank=True,
    )

    # The identities mentioned in the post
    mentions = models.ManyToManyField(
        "users.Identity",
        related_name="posts_mentioning",
        blank=True,
    )

    # Hashtags in the post
    hashtags = models.JSONField(blank=True, null=True)

    emojis = models.ManyToManyField(
        "activities.Emoji",
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

    objects = PostManager()

    class Meta:
        indexes = [
            GinIndex(fields=["hashtags"], name="hashtags_gin"),
            GinIndex(
                SearchVector("content", config="english"),
                name="content_vector_gin",
            ),
            models.Index(
                fields=["visibility", "local", "published"],
                name="ix_post_local_public_published",
            ),
            models.Index(
                fields=["visibility", "local", "created"],
                name="ix_post_local_public_created",
            ),
            models.Index(fields=["url"], name="activities_post_url_idx"),
        ]

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

        def get_scheme(self, url):
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

    ### Content cleanup and extraction ###
    def clean_type_data(self, value):
        PostTypeData.model_validate(value)

    def _safe_content_note(self, *, local: bool = True):
        return ContentRenderer(local=local).render_post(self.content, self)

    @property
    def safe_content_note_local(self):
        """Render just the note body for request-aware typed templates."""
        return self._rewrite_neodb_urls(self._safe_content_note(local=True))

    def _safe_content_question(self, *, local: bool = True):
        if local:
            context = {
                "post": self,
                "sanitized_content": self._safe_content_note(local=local),
                "local_display": local,
            }
            return loader.render_to_string("activities/_type_question.html", context)
        else:
            return ContentRenderer(local=local).render_post(self.content, self)

    def _safe_content_typed(self, *, local: bool = True):
        context = {
            "post": self,
            "sanitized_content": self._safe_content_note(local=local),
            "local_display": local,
        }
        return loader.render_to_string(
            (
                f"activities/_type_{self.type.lower()}.html",
                "activities/_type_unknown.html",
            ),
            context,
        )

    @property
    def converted_preview_card(self):
        if self.type not in self.CONVERTED_TYPES or not self.preview_card_id:
            return None
        return self.preview_card if self.preview_card.state == "fetched" else None

    @property
    def article_cover_url(self) -> str | None:
        """Lead image URL for an Article, if its AS object carries one.

        The AS ``image`` may be a URL string, an Image/Link object, or an array
        of either; normalize to a single http(s) URL for the web article view.
        Returns None for non-Article posts or when no usable image is present.
        This is used by the web templates only and is not folded into the
        Mastodon API status content.
        """
        if self.type != self.Types.article:
            return None
        obj = self.type_data.get("object") if isinstance(self.type_data, dict) else None
        if not isinstance(obj, dict):
            return None
        url, _ = _ap_link(obj.get("image"))
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
        return None

    def safe_content(self, *, local: bool = True):
        if self.type in self.CONVERTED_TYPES:
            return self._safe_content_note(local=local)
        func = getattr(
            self, f"_safe_content_{self.type.lower()}", self._safe_content_typed
        )
        if callable(func):
            return func(local=local)
        return self._safe_content_note(local=local)  # fallback

    @staticmethod
    def _rewrite_neodb_urls(content: str) -> str:
        """Rewrite ~neodb~ placeholder URLs to local search URLs for display."""
        return re.sub(
            r'href="(https?://[^/"]+)/~neodb~(/[^"]+)"',
            'href="https://' + settings.SETUP.MAIN_DOMAIN + r'/search?r=1&q=\1\2"',
            content,
        )

    def safe_content_local(self):
        """
        Returns the content formatted for local display
        """
        return self._rewrite_neodb_urls(self.safe_content(local=True))

    def safe_content_remote(self):
        """
        Returns the content formatted for remote consumption
        """
        return self.safe_content(local=False)

    def safe_content_api(self) -> str:
        """
        Returns the content formatted for API consumption
        (with ~neodb~ URL rewrites, but otherwise same as remote)
        """
        return self._rewrite_neodb_urls(self.safe_content(local=False))

    def summary_class(self) -> str:
        """
        Returns a CSS class name to identify this summary value
        """
        if not self.summary:
            return ""
        return f"summary-{self.id}"

    def content_preview(self, length=80):
        preview = html.unescape(strip_tags(self.content))[: length + 1]
        if len(preview) > length:
            preview = preview[:length] + "…"
        return preview

    @property
    def stats_with_defaults(self):
        """
        Returns the stats dict with counts of likes/etc. in it
        """
        return {
            "likes": self.stats.get("likes", 0) if self.stats else 0,
            "boosts": self.stats.get("boosts", 0) if self.stats else 0,
            "replies": self.stats.get("replies", 0) if self.stats else 0,
        }

    ### Local creation/editing ###
    def neodb_sync_local(
        self: "Post", reply_to: "Post | None", content: str, create: bool
    ):
        if create:
            func = "takahe.ap_handlers.post_created"
        else:
            func = "takahe.ap_handlers.post_edited"
        settings.NEODB_MQ.enqueue(func, self.pk, {"raw_content": content})

    @classmethod
    def create_local(
        cls,
        author: Identity,
        content: str,
        summary: str | None = None,
        sensitive: bool = False,
        visibility: int = Visibilities.public,
        reply_to: Optional["Post"] = None,
        quote: Optional["Post"] = None,
        attachments: list | None = None,
        question: dict | None = None,
        language: str | None = None,
        application=None,
    ) -> "Post":
        with transaction.atomic():
            # Find mentions in this post
            mentions = cls.mentions_from_content(content, author)
            if reply_to:
                mentions.add(reply_to.author)
                # Maintain local-only for replies
                if reply_to.visibility == reply_to.Visibilities.local_only:
                    visibility = reply_to.Visibilities.local_only
            # Find emoji in this post
            emojis = Emoji.emojis_from_content(content, None)
            # Strip all unwanted HTML and apply linebreaks filter, grabbing hashtags on the way
            parser = FediverseHtmlParser(linebreaks_filter(content), find_hashtags=True)
            hashtags = (
                sorted([tag[: Hashtag.MAXIMUM_LENGTH] for tag in parser.hashtags])
                or None
            )
            if language is None or language == "":
                language = author.config_identity.preferred_posting_language

            # Make the Post object
            post = cls.objects.create(
                author=author,
                content=parser.html,
                summary=summary or None,
                sensitive=bool(summary) or sensitive,
                local=True,
                visibility=visibility,
                hashtags=hashtags,
                in_reply_to=reply_to.object_uri if reply_to else None,
                quote_url=quote.object_uri if quote else None,
                language=language,
                application=application,
            )
            post.object_uri = post.urls.object_uri
            post.url = post.absolute_object_uri()
            post.mentions.set(mentions)
            post.emojis.set(emojis)
            if attachments:
                post.attachments.set(attachments)
            if question:
                post.type = question["type"]
                post.type_data = PostTypeData(root=question).root
                if isinstance(post.type_data, QuestionData):
                    # Baseline for detecting when a tally Update is due
                    post.type_data.last_distributed_tally = post.type_data.tally
            post.save()
            # Assign to conversation if this is a direct message
            if visibility == cls.Visibilities.mentioned:
                from activities.models.conversation import Conversation

                Conversation.update_for_post(post)
            # Recalculate parent stats for replies
            if reply_to:
                reply_to.calculate_stats()
        post.neodb_sync_local(reply_to, content, True)
        return post

    def edit_local(
        self,
        content: str,
        summary: str | None = None,
        sensitive: bool | None = None,
        attachments: list | None = None,
        attachment_attributes: list | None = None,
        language: str | None = None,
        question: dict | None = None,
    ):
        with transaction.atomic():
            # Serialize against concurrent vote handling, which also
            # rewrites type_data
            Post.objects.select_for_update().get(pk=self.pk)
            # Strip all HTML and apply linebreaks filter
            parser = FediverseHtmlParser(linebreaks_filter(content), find_hashtags=True)
            self.content = parser.html
            self.hashtags = (
                sorted([tag[: Hashtag.MAXIMUM_LENGTH] for tag in parser.hashtags])
                or None
            )
            self.summary = summary or None
            self.sensitive = bool(summary) if sensitive is None else sensitive
            if language is None or language == "":
                language = self.author.config_identity.preferred_posting_language
            self.language = language
            self.edited = timezone.now()
            self.mentions.set(self.mentions_from_content(content, self.author))
            self.emojis.set(Emoji.emojis_from_content(content, None))
            self.attachments.set(attachments or [])
            if question is not None or self.type == Post.Types.question:
                self.apply_question_edit(question)
            self.save()

            for attrs in attachment_attributes or []:
                attachment = next(
                    (a for a in attachments or [] if str(a.id) == attrs.id), None
                )
                if attachment is None:
                    continue
                attachment.name = attrs.description
                attachment.save()

            self.transition_perform(PostStates.edited)
        self.neodb_sync_local(self.in_reply_to_post(), content, False)

    def apply_question_edit(self, question: dict | None) -> None:
        """
        Applies a poll change during a local post edit: adds, replaces or
        removes the poll. Changing the options or the mode invalidates all
        previous votes (matching Mastodon).
        """
        from activities.models.post_interaction import PostInteraction

        if question is None:
            # The poll was removed by the edit
            self.type = Post.Types.note
            self.type_data = None
            self.interactions.filter(type=PostInteraction.Types.vote).delete()
            return
        old = self.type_data if isinstance(self.type_data, QuestionData) else None
        new_data = PostTypeData(root=question).root
        significantly_changed = (
            old is None
            or old.mode != new_data.mode
            or [option.name for option in (old.options or [])]
            != [option.name for option in (new_data.options or [])]
        )
        if significantly_changed:
            self.interactions.filter(type=PostInteraction.Types.vote).delete()
        self.type = Post.Types.question
        self.type_data = new_data
        self.calculate_type_data(save=False)

    @classmethod
    def mentions_from_content(cls, content, author) -> set[Identity]:
        mention_hits = FediverseHtmlParser(content, find_mentions=True).mentions
        mentions = set()
        for handle in mention_hits:
            handle = handle.lower()
            if "@" in handle:
                username, domain = handle.split("@", 1)
            else:
                username = handle
                domain = author.domain_id
            identity = Identity.by_username_and_domain(
                username=username,
                domain=domain,
                fetch=True,
            )
            if (
                identity is not None
                and not identity.deleted
                and not Block.maybe_get(
                    source=identity, target=author, require_active=True
                )
            ):
                mentions.add(identity)
        return mentions

    def ensure_hashtags(self) -> None:
        """
        Ensure any of the already parsed hashtags from this Post
        have a corresponding Hashtag record.
        """
        # Ensure hashtags
        if self.hashtags:
            for hashtag in self.hashtags:
                Hashtag.ensure_hashtag(hashtag, update=True)

    def calculate_stats(self, save=True):
        """
        Recalculates our stats dict
        """
        from activities.models import PostInteraction, PostInteractionStates

        self.stats = {
            "likes": self.interactions.filter(
                type=PostInteraction.Types.like,
                state__in=PostInteractionStates.group_active(),
            ).count(),
            "boosts": self.interactions.filter(
                type=PostInteraction.Types.boost,
                state__in=PostInteractionStates.group_active(),
            ).count(),
            # only count replies visible to post's author
            "replies": Post.objects.filter(in_reply_to=self.object_uri)
            .not_hidden()
            .visible_to(self.author, True)
            .count(),
        }
        if save:
            self.save()

    def recalculate_parent_stats(self) -> None:
        """If this is a reply, refresh the parent's cached stats (replies_count)."""
        parent = self.in_reply_to_post()
        if parent:
            parent.calculate_stats()

    def calculate_type_data(self, save=True):
        """
        Recalculate type_data (used mostly for poll votes)
        """
        from activities.models import PostInteraction, PostInteractionStates

        if self.local and isinstance(self.type_data, QuestionData):
            active_votes = self.interactions.filter(
                type=PostInteraction.Types.vote,
                state__in=PostInteractionStates.group_active(),
            )
            self.type_data.voter_count = (
                active_votes.values("identity").distinct().count()
            )

            for option in self.type_data.options:
                option.votes = active_votes.filter(
                    value=vote_value(option.name),
                ).count()
        if save:
            self.save()

    def question_local_voters(self) -> list[Identity]:
        """
        Local identities that voted on this poll (for end-of-poll notifications)
        """
        from activities.models import PostInteraction, PostInteractionStates

        return list(
            Identity.objects.filter(
                interactions__post=self,
                interactions__type=PostInteraction.Types.vote,
                interactions__state__in=PostInteractionStates.group_active(),
                local=True,
            ).distinct()
        )

    def refresh_question_from_remote(self) -> "Post | None":
        """
        Re-fetches a remote poll from its origin to pick up fresh tallies.
        Returns the updated Post, or None if it could not be refreshed.
        """
        if self.local or self.type != Post.Types.question:
            return None
        try:
            response = SystemActor().signed_request(method="get", uri=self.object_uri)
        except httpx.HTTPError, ssl.SSLCertVerificationError, ValueError, TypeError:
            return None
        if response.status_code >= 400:
            return None
        try:
            json_data = json_from_response(response)
            ap_data = canonicalise(json_data, include_security=True, outbound=False)
            post = Post.by_ap(ap_data, create=False, update=True)
        except (
            json.JSONDecodeError,
            ValueError,
            JsonLdError,
            ActivityPubFormatError,
            Post.DoesNotExist,
        ):
            return None
        # by_ap stamped type_data.last_fetched while applying the update
        return post

    def refresh_question_if_stale(self) -> "Post":
        """
        Re-fetches a remote poll when its tallies may be out of date:
        more than a minute since the last fetch, and not yet fetched
        after the poll ended (final results).
        """
        question = self.type_data
        if self.local or not isinstance(question, QuestionData):
            return self
        if question.last_fetched:
            if (timezone.now() - question.last_fetched).total_seconds() < 60:
                return self
            end_time = question.effective_end_time
            if end_time and question.last_fetched >= end_time:
                return self
        return self.refresh_question_from_remote() or self

    ### ActivityPub (outbound) ###

    def to_ap(self) -> dict:
        """
        Returns the AP JSON for this object
        """
        self.author.ensure_uris()
        value = {
            "to": [],
            "cc": [],
            "type": self.type,
            "id": self.object_uri,
            "published": format_ld_date(self.published),
            "attributedTo": self.author.actor_uri,
            "content": self.safe_content_remote(),
            "sensitive": self.sensitive,
            "url": self.absolute_object_uri(),
            "tag": [],
            "attachment": [],
        }
        if self.language != "":
            value["contentMap"] = {
                self.language: value["content"],
            }
        if self.type == Post.Types.question and self.type_data:
            question = self.type_data
            expired = question.is_expired
            totals_hidden = question.hide_totals and not expired
            value[question.mode] = [
                {
                    "name": option.name,
                    "type": option.type,
                    "replies": {
                        "type": "Collection",
                        "totalItems": 0 if totals_hidden else option.votes,
                    },
                }
                for option in question.options or []
            ]
            value["toot:votersCount"] = question.voter_count
            if question.end_time:
                value["endTime"] = format_ld_date(question.end_time)
            if expired and question.effective_end_time:
                value["closed"] = format_ld_date(question.effective_end_time)
        if self.summary:
            value["summary"] = self.summary
        if self.in_reply_to:
            value["inReplyTo"] = self.in_reply_to
        if self.quote_url:
            value["quote"] = self.quote_url
            value["quoteUrl"] = self.quote_url
            value["quoteUri"] = self.quote_url
            value["_misskey_quote"] = self.quote_url
            value["tag"].append(
                {
                    "type": "Link",
                    "mediaType": (
                        "application/ld+json;"
                        ' profile="https://www.w3.org/'
                        'ns/activitystreams"'
                    ),
                    "href": self.quote_url,
                }
            )
        # Interaction policy: allow quoting of public/unlisted posts
        if self.visibility in (
            self.Visibilities.public,
            self.Visibilities.unlisted,
        ):
            value["interactionPolicy"] = {
                "canQuote": {
                    "automaticApproval": ["as:Public"],
                }
            }
        if self.edited:
            value["updated"] = format_ld_date(self.edited)
        # Targeting
        if self.visibility == self.Visibilities.public:
            value["to"].append("as:Public")
        elif self.visibility == self.Visibilities.unlisted:
            value["cc"].append("as:Public")
        elif (
            self.visibility == self.Visibilities.followers and self.author.followers_uri
        ):
            value["to"].append(self.author.followers_uri)
        # Mentions
        for mention in self.mentions.all():
            value["tag"].append(mention.to_ap_tag())
            value["cc"].append(mention.actor_uri)
        # Hashtags
        for hashtag in self.hashtags or []:
            value["tag"].append(
                {
                    "href": f"https://{self.author.domain.uri_domain}/tags/{hashtag}/",
                    "name": f"#{hashtag}",
                    "type": "Hashtag",
                }
            )
        # Emoji
        for emoji in self.emojis.all():
            value["tag"].append(emoji.to_ap_tag())
        # Attachments
        for attachment in self.attachments.all():
            value["attachment"].append(attachment.to_ap())
        # Replies collection (only for local posts with a URI)
        if self.local and self.object_uri:
            replies_uri = self.object_uri + "replies/"
            replies_count = self.stats.get("replies", 0) if self.stats else 0
            value["replies"] = {
                "id": replies_uri,
                "type": "Collection",
                "totalItems": replies_count,
                "first": {
                    "type": "CollectionPage",
                    "partOf": replies_uri,
                    "items": list(
                        Post.objects.filter(
                            in_reply_to=self.object_uri,
                            visibility__in=[
                                Post.Visibilities.public,
                                Post.Visibilities.unlisted,
                            ],
                        )
                        .not_hidden()
                        .order_by("published")
                        .values_list("object_uri", flat=True)[:50]
                    ),
                },
            }
        # Remove fields if they're empty
        for field in ["to", "cc", "tag", "attachment"]:
            if not value[field]:
                del value[field]
        if isinstance(self.type_data, dict) and "object" in self.type_data:
            always_merger.merge(value, self.type_data["object"])
        return value

    def to_create_ap(self):
        """
        Returns the AP JSON to create this object
        """
        object = self.to_ap()
        return {
            "to": object.get("to", []),
            "cc": object.get("cc", []),
            "type": "Create",
            "id": self.object_uri + "#create",
            "actor": self.author.actor_uri,
            "object": object,
        }

    def to_update_ap(self):
        """
        Returns the AP JSON to update this object
        """
        object = self.to_ap()
        # Each revision needs its own activity ID - some servers (e.g.
        # Pleroma) deduplicate activities by ID, and polls now send
        # several Updates (tallies, closing) over their lifetime.
        if self.updated:
            update_id = f"{self.object_uri}#updates/{int(self.updated.timestamp())}"
        else:
            update_id = self.object_uri + "#update"
        return {
            "to": object.get("to", []),
            "cc": object.get("cc", []),
            "type": "Update",
            "id": update_id,
            "actor": self.author.actor_uri,
            "object": object,
        }

    def to_delete_ap(self):
        """
        Returns the AP JSON to create this object
        """
        object = self.to_ap()
        return {
            "to": object.get("to", []),
            "cc": object.get("cc", []),
            "type": "Delete",
            "id": self.object_uri + "#delete",
            "actor": self.author.actor_uri,
            "object": object,
        }

    def get_targets(self) -> Iterable[Identity]:
        """
        Returns a list of Identities that need to see posts and their changes
        """
        targets = set()
        for mention in self.mentions.all():
            targets.add(mention)
        if self.visibility in [Post.Visibilities.public, Post.Visibilities.unlisted]:
            # deliver edit to all previously interacted to this post
            for interaction in self.interactions.all():
                targets.add(interaction.identity)
            # deliver to all hashtag followers
            if self.hashtags:
                for follow in HashtagFollow.objects.by_hashtags(
                    self.hashtags
                ).prefetch_related("identity"):
                    targets.add(follow.identity)
        # Then, if it's not mentions only, also deliver to followers
        if self.visibility != Post.Visibilities.mentioned:
            for follower in (
                self.author.inbound_follows.filter(
                    state__in=FollowStates.group_active()
                )
                .exclude(source__state=IdentityStates.connection_issue)
                .select_related("source")
            ):
                targets.add(follower.source)

        # If it quotes a post, include the quoted post's author
        if self.quote_url:
            quoted_post = (
                Post.objects.filter(object_uri=self.quote_url)
                .select_related("author")
                .first()
            )
            if quoted_post:
                targets.add(quoted_post.author)
        # If it's a reply, always include the original author if we know them
        reply_post = self.in_reply_to_post()
        if reply_post:
            targets.add(reply_post.author)
            # And if it's a reply to one of our own, we have to re-fan-out to
            # the original author's followers
            if reply_post.author.local:
                for follower in reply_post.author.inbound_follows.filter(
                    state__in=FollowStates.group_active()
                ).select_related("source"):
                    targets.add(follower.source)
        # If this is a remote post or local-only, filter to only include
        # local identities
        if not self.local or self.visibility == Post.Visibilities.local_only:
            targets = {target for target in targets if target.local}
        # If it's a local post, include the author
        if self.local:
            targets.add(self.author)
        # Fetch the author's full blocks and remove them as targets
        blocks = (
            self.author.outbound_blocks.active()
            .filter(mute=False)
            .select_related("target")
        )
        for block in blocks:
            try:
                targets.remove(block.target)
            except KeyError:
                pass
        # Now dedupe the targets based on shared inboxes (we only keep one per
        # shared inbox)
        deduped_targets = set()
        shared_inboxes = set()
        for target in targets:
            if target.local or not target.shared_inbox_uri:
                deduped_targets.add(target)
            elif target.shared_inbox_uri not in shared_inboxes:
                shared_inboxes.add(target.shared_inbox_uri)
                deduped_targets.add(target)
            else:
                # Their shared inbox is already being sent to
                pass
        return deduped_targets

    ### ActivityPub (inbound) ###

    @staticmethod
    def _primary_attributed_to(value):
        """Normalize an AS ``attributedTo`` value to a single URI string.

        Accepts a URI string, an embedded actor object ({"id": ...}), or
        an array of either. WriteFreely sends ``[author_person, blog_group]``
        for blog posts and other servers may invert the order, so when
        given a list we prefer the first URI that already resolves to a
        non-Group identity locally (cheap DB lookup, no HTTP). If none of
        the candidates are known yet we fall back to the first URI, which
        matches the WriteFreely convention. Returns ``None`` for anything
        unrecognizable so the caller's structural check can raise.
        """
        if isinstance(value, list):
            uris: list[str] = []
            for v in value:
                if isinstance(v, dict):
                    v = v.get("id")
                if isinstance(v, str):
                    uris.append(v)
            if not uris:
                return None
            # Pick the first candidate already known locally that's not
            # a Group/Organization. Stays robust if the author has been
            # seen previously (e.g. fetched as a follow or earlier post),
            # regardless of the order the upstream emits the list.
            known = (
                Identity.objects.filter(actor_uri__in=uris)
                .exclude(actor_type__in=["group", "organization"])
                .values_list("actor_uri", flat=True)
            )
            known_set = set(known)
            for uri in uris:
                if uri in known_set:
                    return uri
            return uris[0]
        if isinstance(value, dict):
            value = value.get("id")
        return value if isinstance(value, str) else None

    @classmethod
    def _primary_post_type(cls, value):
        """Select the Takahē post type from an AS type string or array."""

        if isinstance(value, str):
            return value
        if isinstance(value, list):
            strings = [item for item in value if isinstance(item, str)]
            for supported_type in cls.Types.values:
                if supported_type in strings:
                    return supported_type
            if strings:
                return strings[0]
        return None

    @classmethod
    def by_ap(cls, data, create=False, update=False, fetch_author=False) -> "Post":
        """
        Retrieves a Post instance by its ActivityPub JSON object.

        Optionally creates one if it's not present.
        Raises DoesNotExist if it's not found and create is False,
        or it's from a blocked domain.
        """
        try:
            # Normalize attributedTo to a single URI string. Per the
            # ActivityStreams spec it may be a URI, an embedded actor
            # object, or an array of either. WriteFreely emits
            # ``[author_person, blog_group]`` for blog posts; the author
            # is conventionally first, so we take the head.
            data["attributedTo"] = cls._primary_attributed_to(data.get("attributedTo"))
            data["type"] = cls._primary_post_type(data.get("type"))
            # Ensure data has the primary fields of all Posts
            if (
                not isinstance(data["id"], str)
                or not isinstance(data["attributedTo"], str)
                or not isinstance(data["type"], str)
            ):
                raise TypeError()
            # Ensure the domain of the object's actor and ID match to prevent injection
            if urlparse(data["id"]).hostname != urlparse(data["attributedTo"]).hostname:
                raise ActivityPubFormatError(
                    "Object's ID domain is different to its author"
                )
        except (TypeError, KeyError) as ex:
            raise cls.DoesNotExist(
                "Object data is not a recognizable ActivityPub object"
            ) from ex

        # Do we have one with the right ID?
        created = False
        try:
            post: Post = cls.objects.select_related("author__domain").get(
                object_uri=data["id"]
            )
        except cls.DoesNotExist:
            if create:
                # Resolve the author
                author = Identity.by_actor_uri(data["attributedTo"], create=create)
                # If the author is not fetched yet, try again later
                if author.domain is None:
                    if fetch_author:
                        if not author.fetch_actor() or author.domain is None:
                            raise TryAgainLater()
                    else:
                        raise TryAgainLater()
                # If the post is from a blocked domain, stop and drop
                if author.domain.recursively_blocked():
                    raise cls.DoesNotExist("Post is from a blocked domain")
                # parallelism may cause another simultaneous worker thread
                # to try to create the same post - so watch for that and
                # try to avoid failing the entire transaction
                try:
                    # wrapped in a transaction to avoid breaking the outer
                    # transaction
                    with transaction.atomic():
                        post = cls.objects.create(
                            object_uri=data["id"],
                            author=author,
                            content="",
                            local=False,
                            type=data["type"],
                        )
                        created = True
                except IntegrityError:
                    # despite previous checks, a parallel thread managed
                    # to create the same object already
                    raise TryAgainLater()
            else:
                raise cls.DoesNotExist(f"No post with ID {data['id']}", data)
        if update or created:
            post.type = data["type"]
            post.url, _ = _ap_link(data.get("url"), preferred_media_type="text/html")
            post.url = post.url or data["id"]
            if post.type == cls.Types.question:
                post.type_data = PostTypeData(root=data).root
                if not post.local and isinstance(post.type_data, QuestionData):
                    # Fresh data from the origin counts as a fetch, so
                    # a first API view doesn't immediately re-fetch it
                    post.type_data.last_fetched = timezone.now()
            elif post.type == cls.Types.article or "relatedWith" in data:
                # Preserve the full AS Article (name, summary, source, url,
                # tags, etc.) so the NeoDB Post-only renderer can show a
                # title-card teaser without a local Article row. Mirrors the
                # ``{"object": data}`` shape used by NeoDB's relatedWith
                # envelopes (Reviews/Marks) and by locally-authored Articles.
                post.type_data = {"object": data}
            elif post.type in cls.CONVERTED_TYPES:
                # Keep every field, including Event times/location and media
                # metadata, while presenting the object as a normal status to
                # Mastodon clients.
                post.type_data = {"object": data}
            else:
                post.type_data = None
            if post.type in cls.CONVERTED_TYPES:
                post.content = _converted_post_content(data, post.url)
                # Mastodon includes a converted object's summary in its status
                # text and does not expose it as the status content warning.
                post.summary = None
            else:
                try:
                    # Some fediverse objects do not have content; this should
                    # not make the whole activity fail.
                    post.content = get_value_or_map(data, "content", "contentMap") or ""
                except ActivityPubFormatError as err:
                    logger.warning("%s on %s", err, post.url)
                    post.content = ""
                # Document types have names, not summaries.
                post.summary = data.get("summary") or data.get("name")
                if not post.content and post.summary:
                    post.content = post.summary
                    post.summary = None
            post.sensitive = data.get("sensitive", False)
            post.published = parse_ld_date(data.get("published")) or timezone.now()
            post.edited = parse_ld_date(data.get("updated"))
            in_reply_to = data.get("inReplyTo")
            if isinstance(in_reply_to, dict):
                in_reply_to = in_reply_to.get("id")
            post.in_reply_to = in_reply_to
            # Quote URL - check properties in priority order (FEP-044f).
            # BookWyrm overloads `quote` with the HTML quotation text rather
            # than a URL, so only accept http(s) URLs that fit the column.
            post.quote_url = None
            for key in ("quote", "_misskey_quote", "quoteUrl", "quoteUri"):
                val = data.get(key)
                if isinstance(val, str):
                    if _is_quote_uri(val):
                        post.quote_url = val
                    break
                elif isinstance(val, dict):
                    if val.get("type") == "Tombstone":
                        break
                    uri = val.get("id")
                    if isinstance(uri, str) and _is_quote_uri(uri):
                        post.quote_url = uri
                    break
            # FEP-e232 tag Link fallback
            if not post.quote_url:
                for tag in get_list(data, "tag"):
                    href = tag.get("href")
                    if (
                        tag.get("type") == "Link"
                        and isinstance(tag.get("mediaType"), str)
                        and tag["mediaType"].startswith("application/ld+json")
                        and isinstance(href, str)
                        and _is_quote_uri(href)
                    ):
                        post.quote_url = href
                        break
            # Strip FEP-044f fallback quote-inline span from content
            if post.quote_url:
                post.content = re.sub(
                    r'<span class="quote-inline">.*?</span>',
                    "",
                    post.content,
                    flags=re.DOTALL,
                )
            post.language = get_language(data) or ""
            # Mentions and hashtags
            post.hashtags = []
            for tag in get_list(data, "tag"):
                tag_type = tag["type"].lower()
                if tag_type == "mention":
                    mention_identity = Identity.by_actor_uri(tag["href"], create=True)
                    post.mentions.add(mention_identity)
                elif tag_type in ["_:hashtag", "hashtag"]:
                    # kbin produces tags with 'tag' instead of 'name'
                    if "tag" in tag and "name" not in tag:
                        name = get_value_or_map(tag, "tag", "tagMap")
                    else:
                        name = get_value_or_map(tag, "name", "nameMap")
                    post.hashtags.append(
                        name.lower().lstrip("#")[: Hashtag.MAXIMUM_LENGTH]
                    )
                elif tag_type in ["toot:emoji", "emoji"]:
                    try:
                        emoji = Emoji.by_ap_tag(post.author.domain, tag, create=True)
                        post.emojis.add(emoji)
                    except KeyError, ValueError:
                        pass
                else:
                    # Various ActivityPub implementations and proposals introduced tag
                    # types, e.g. Edition in Bookwyrm and Link in fep-e232 Object Links
                    # it should be safe to ignore (and log) them before a full support
                    pass
            # Visibility and to
            # (a post is public if it's to:public, otherwise it's unlisted if
            # it's cc:public, otherwise it's more limited)
            to = [x.lower() for x in get_list(data, "to")]
            cc = [x.lower() for x in get_list(data, "cc")]
            post.visibility = Post.Visibilities.mentioned
            if "public" in to or "as:public" in to:
                post.visibility = Post.Visibilities.public
            elif "public" in cc or "as:public" in cc:
                post.visibility = Post.Visibilities.unlisted
            elif post.author.followers_uri in to:
                post.visibility = Post.Visibilities.followers
            # Attachments
            # These have no IDs, so we have to wipe them each time
            post.attachments.all().delete()
            for attachment in get_list(data, "attachment"):
                if not isinstance(attachment, dict):
                    continue
                if "url" not in attachment and "href" in attachment:
                    # Links have hrefs, while other Objects have urls
                    attachment["url"] = attachment["href"]
                attachment_url, link_metadata = _ap_link(attachment.get("url"))
                if "focalPoint" in attachment:
                    try:
                        focal_x, focal_y = attachment["focalPoint"]
                    except ValueError, TypeError:
                        focal_x, focal_y = None, None
                else:
                    focal_x, focal_y = None, None
                mimetype = attachment.get("mediaType") or link_metadata.get("mediaType")
                if not mimetype or not isinstance(mimetype, str):
                    if not attachment_url:
                        raise ActivityPubFormatError(
                            f"No URL present on attachment in {post.url}"
                        )
                    mimetype, _ = mimetypes.guess_type(attachment_url)
                    if not mimetype:
                        mimetype = "application/octet-stream"
                if not attachment_url:
                    raise ActivityPubFormatError(
                        f"No URL present on attachment in {post.url}"
                    )
                post.attachments.create(
                    remote_url=attachment_url,
                    mimetype=mimetype,
                    name=attachment.get("name") or attachment.get("summary"),
                    width=_positive_int(attachment.get("width")),
                    height=_positive_int(attachment.get("height")),
                    blurhash=attachment.get("blurhash"),
                    focal_x=focal_x,
                    focal_y=focal_y,
                )
            # Calculate stats in case we have existing replies
            post.calculate_stats(save=False)
            with transaction.atomic():
                # if we don't commit the transaction here, there's a chance
                # the parent fetch below goes into an infinite loop
                post.save()

            if post.type in cls.CONVERTED_TYPES:
                post._sync_converted_preview_card(data)

            # Assign to conversation if this is a direct message
            if post.visibility == Post.Visibilities.mentioned:
                from activities.models.conversation import Conversation

                Conversation.update_for_post(post)

            # Potentially schedule a fetch of the reply parent, and recalculate
            # its stats if it's here already.
            if post.in_reply_to:
                depth = data.get("_fetch_depth", 0)
                try:
                    parent = cls.by_object_uri(post.in_reply_to)
                except cls.DoesNotExist:
                    try:
                        cls.ensure_object_uri(
                            post.in_reply_to,
                            reason=post.object_uri,
                            depth=depth + 1,
                        )
                    except ValueError:
                        logger.warning(
                            "Cannot fetch ancestor of Post=%s, ancestor_uri=%s",
                            post.pk,
                            post.in_reply_to,
                        )
                else:
                    parent.calculate_stats()
            if "relatedWith" in data:
                settings.NEODB_MQ.enqueue(
                    "takahe.ap_handlers.post_fetched", post.pk, data
                )
            # If the post has a replies collection, queue a fetch of the replies
            replies = data.get("replies")
            if replies and not post.local:
                replies_uri = None
                if isinstance(replies, str):
                    replies_uri = replies
                elif isinstance(replies, dict):
                    replies_uri = replies.get("id")
                if replies_uri and replies_uri.startswith(("https://", "http://")):
                    InboxMessage.create_internal(
                        {
                            "type": "FetchReplies",
                            "object": replies_uri,
                            "post_uri": post.object_uri,
                        }
                    )
        return post

    @classmethod
    def by_object_uri(
        cls, object_uri, fetch=False, fetch_as=None, fetch_depth: int = 0
    ) -> "Post":
        """
        Gets the post by URI - either looking up locally, or fetching
        from the other end if it's not here.
        """
        if not object_uri:
            raise cls.DoesNotExist("No object_uri provided")
        try:
            return cls.objects.get(object_uri=object_uri)
        except cls.DoesNotExist:
            if fetch:
                try:
                    response = (fetch_as or SystemActor()).signed_request(
                        method="get", uri=object_uri
                    )
                except (
                    httpx.HTTPError,
                    ssl.SSLCertVerificationError,
                    ValueError,
                    TypeError,
                ):
                    raise cls.DoesNotExist(f"Could not fetch {object_uri}")
                if response.status_code in [404, 410]:
                    raise cls.DoesNotExist(f"No post at {object_uri}")
                if response.status_code >= 500:
                    raise cls.DoesNotExist(f"Server error fetching {object_uri}")
                if response.status_code >= 400:
                    raise cls.DoesNotExist(
                        f"Error fetching post from {object_uri}: {response.status_code}",
                        {response.content},
                    )
                try:
                    json_data = json_from_response(response)
                    ap_data = canonicalise(
                        json_data, include_security=True, outbound=False
                    )
                    ap_data["_fetch_depth"] = fetch_depth
                    post = cls.by_ap(
                        ap_data,
                        create=True,
                        update=True,
                        fetch_author=True,
                    )
                except (json.JSONDecodeError, ValueError, JsonLdError) as err:
                    raise cls.DoesNotExist(
                        f"Invalid ld+json response for {object_uri}"
                    ) from err
                # We may need to fetch the author too
                if post.author.state == IdentityStates.outdated:
                    post.author.fetch_actor()
                return post
            else:
                raise cls.DoesNotExist(f"Cannot find Post with URI {object_uri}")

    MAX_ANCESTOR_FETCH_DEPTH = 20

    @classmethod
    def ensure_object_uri(
        cls, object_uri: str, reason: str | None = None, depth: int = 0
    ):
        """
        Sees if the post is in our local set, and if not, schedules a fetch
        for it (in the background). Depth limits recursive ancestor fetching.
        """
        if not object_uri or "://" not in object_uri:
            raise ValueError("URI missing or invalid")
        if depth >= cls.MAX_ANCESTOR_FETCH_DEPTH:
            logger.info(
                "Skipping fetch of %s: depth %d exceeds limit %d",
                object_uri,
                depth,
                cls.MAX_ANCESTOR_FETCH_DEPTH,
            )
            return
        try:
            cls.by_object_uri(object_uri)
        except cls.DoesNotExist:
            InboxMessage.create_internal(
                {
                    "type": "FetchPost",
                    "object": object_uri,
                    "reason": reason,
                    "depth": depth,
                }
            )

    @classmethod
    def handle_create_ap(cls, data):
        """
        Handles an incoming create request
        """
        from . import TimelineEvent

        with transaction.atomic():
            # Ensure the Create actor is among the Post's attributedTo
            # entries. WriteFreely sends a list ``[author, blog]`` and the
            # outer ``actor`` may be either one; accept any match rather
            # than only the first URI.
            attributed = data["object"]["attributedTo"]
            if not isinstance(attributed, list):
                attributed = [attributed]
            attributed_ids = {cls._primary_attributed_to(v) for v in attributed} - {
                None
            }
            if data["actor"] not in attributed_ids:
                raise ActorMismatchError(
                    "Create actor does not match its Post object", data
                )
            # Create it, stator will fan it out locally
            post = cls.by_ap(
                data["object"], create=True, update=True, fetch_author=True
            )
            if post.author and post.author.actor_type == "group":
                # for remote group, save it in this actor's timeline so we can show later
                TimelineEvent.add_post(post.author, post)

    @classmethod
    def handle_update_ap(cls, data):
        """
        Handles an incoming update request
        """
        with transaction.atomic():
            # Ensure the Update actor is among the Post's attributedTo
            # entries (see ``handle_create_ap`` for the WriteFreely-list
            # rationale).
            attributed = data["object"]["attributedTo"]
            if not isinstance(attributed, list):
                attributed = [attributed]
            attributed_ids = {cls._primary_attributed_to(v) for v in attributed} - {
                None
            }
            if data["actor"] not in attributed_ids:
                raise ActorMismatchError(
                    "Update actor does not match its Post object", data
                )
            # Find it and update it
            try:
                cls.by_ap(data["object"], create=False, update=True)
            except cls.DoesNotExist:
                # We don't have a copy - assume we got a delete first and ignore.
                pass

    @classmethod
    def handle_delete_ap(cls, data):
        """
        Handles an incoming delete request
        """
        with transaction.atomic():
            # Is this an embedded object or plain ID?
            if isinstance(data["object"], str):
                object_uri = data["object"]
            else:
                object_uri = data["object"]["id"]
            # Find our post by ID if we have one
            try:
                post = cls.by_object_uri(object_uri)
            except cls.DoesNotExist:
                # It's already been deleted
                return
            # Ensure the actor on the request authored the post
            if not post.author.actor_uri == data["actor"]:
                raise ActorMismatchError("Actor on delete does not match object")
            if post.type == "Article" or (
                post.type_data
                and "object" in post.type_data
                and "relatedWith" in post.type_data.get("object", {})
            ):
                # NeoDB-managed posts (Reviews/Comments via ``relatedWith``,
                # plus standalone Articles which deliberately omit it) need
                # the post_deleted callback so the linked Piece row + search
                # document are cleaned up; otherwise we orphan them.
                settings.NEODB_MQ.enqueue(
                    "takahe.ap_handlers.post_deleted",
                    post.pk,
                    False,
                    post.type_data["object"] if post.type_data else {},
                )
            post.delete()

    @classmethod
    def handle_quote_request_ap(cls, data):
        """
        Handles an incoming QuoteRequest (FEP-044f).
        Auto-accepts for public/unlisted posts, rejects otherwise.
        """
        actor_uri = data.get("actor")
        object_uri = data.get("object")
        if not actor_uri or not object_uri:
            return
        if isinstance(object_uri, dict):
            object_uri = object_uri.get("id")
        if not object_uri:
            return
        try:
            post = cls.by_object_uri(object_uri)
        except cls.DoesNotExist:
            return
        if not post.local:
            return
        requesting_identity = Identity.by_actor_uri(actor_uri)
        if not requesting_identity:
            return
        quoting_post_uri = None
        instrument = data.get("instrument")
        if isinstance(instrument, dict):
            quoting_post_uri = instrument.get("id")
        elif isinstance(instrument, str):
            quoting_post_uri = instrument
        if post.visibility in (
            cls.Visibilities.public,
            cls.Visibilities.unlisted,
        ):
            auth = QuoteAuthorization.objects.create(
                target_post=post,
                interacting_object_uri=quoting_post_uri or actor_uri,
                request_uri=data.get("id"),
            )
            accept_id = Snowflake.generate_post()
            accept_data = {
                "type": "Accept",
                "id": f"{post.author.actor_uri}#accept-{accept_id}",
                "actor": post.author.actor_uri,
                "object": data.get("id", actor_uri),
                "result": auth.to_ap(),
            }
            try:
                post.author.signed_request(
                    method="post",
                    uri=requesting_identity.inbox_uri,
                    body=canonicalise(accept_data),
                )
            except Exception as e:
                logger.warning("Error sending QuoteAuthorization: %s", e)
        else:
            reject_id = Snowflake.generate_post()
            reject_data = {
                "type": "Reject",
                "id": f"{post.author.actor_uri}#reject-{reject_id}",
                "actor": post.author.actor_uri,
                "object": data.get("id", actor_uri),
            }
            try:
                post.author.signed_request(
                    method="post",
                    uri=requesting_identity.inbox_uri,
                    body=canonicalise(reject_data),
                )
            except Exception as e:
                logger.warning("Error sending quote Reject: %s", e)

    @classmethod
    def handle_fetch_internal(cls, data):
        """
        Handles an internal fetch-request inbox message.
        Passes depth through to by_object_uri so ancestor fetching is bounded.
        """
        try:
            uri = data["object"]
            depth = data.get("depth", 0)
            if uri.startswith(("https://", "http://")):
                cls.by_object_uri(uri, fetch=True, fetch_depth=depth)
            else:
                logger.warning("Skipping fetch for non-HTTP URI: %s", uri)
        except cls.DoesNotExist, KeyError:
            pass

    MAX_FETCH_REPLIES = 50

    @classmethod
    def handle_fetch_replies(cls, data):
        """
        Fetches a remote replies collection and queues FetchPost for each
        reply URI not already in the local database.
        """
        replies_uri = data.get("object")
        if not replies_uri or not replies_uri.startswith(("https://", "http://")):
            logger.warning(
                "Skipping fetch for non-HTTP URI: %s", replies_uri or "<empty>"
            )
            return

        try:
            response = SystemActor().signed_request(method="get", uri=replies_uri)
        except httpx.HTTPError, ssl.SSLCertVerificationError, ValueError:
            logger.warning("Failed to fetch replies collection: %s", replies_uri)
            return

        if response.status_code >= 400:
            logger.warning(
                "Error fetching replies collection %s: %d",
                replies_uri,
                response.status_code,
            )
            return

        try:
            collection = json_from_response(response)
        except ValueError, KeyError:
            logger.warning("Invalid JSON from replies collection: %s", replies_uri)
            return

        # Extract items from the first page of the Collection
        items: list = []
        first = collection.get("first")
        if isinstance(first, dict):
            items = first.get("items") or first.get("orderedItems") or []
        elif isinstance(first, str):
            # first is a URL; for simplicity, fall back to top-level items
            items = collection.get("items") or collection.get("orderedItems") or []
        else:
            items = collection.get("items") or collection.get("orderedItems") or []

        # Queue a FetchPost for each URI we don't already have
        count = 0
        for item in items:
            if count >= cls.MAX_FETCH_REPLIES:
                break
            uri = (
                item
                if isinstance(item, str)
                else item.get("id")
                if isinstance(item, dict)
                else None
            )
            if not uri or "://" not in uri:
                continue
            try:
                cls.ensure_object_uri(uri, reason=data.get("post_uri"))
                count += 1
            except ValueError:
                continue

    ### OpenGraph API ###

    def to_opengraph_dict(self) -> dict:
        card = self.converted_preview_card
        title = f"{self.author.name} (@{self.author.handle})"
        description = self.summary or self.safe_content_local()
        image_url = self.author.local_icon_url().absolute
        image_height = 85
        image_width = 85
        if card:
            title = card.title or title
            description = card.description or description
            if card.image_proxy_url:
                image_url = card.image_proxy_url.absolute
                image_height = card.image_height or 85
                image_width = card.image_width or 85
        return {
            "og:title": title,
            "og:type": "article",
            "og:published_time": (self.published or self.created).isoformat(),
            "og:modified_time": (
                self.edited or self.published or self.created
            ).isoformat(),
            "og:description": description,
            "og:image:url": image_url,
            "og:image:height": image_height,
            "og:image:width": image_width,
        }

    def _sync_converted_preview_card(self, data: dict) -> None:
        """Expose converted-object metadata, including icon, as a card."""

        from activities.models.preview_card import PreviewCard, PreviewCardStates

        card_url = self.url or self.object_uri
        if not card_url or not card_url.startswith(("https://", "http://")):
            return

        icon_url, icon = _ap_link(data.get("icon"))
        if icon_url and (
            not icon_url.startswith(("https://", "http://"))
            or len(icon_url) > PreviewCard._meta.get_field("image_url").max_length
        ):
            icon_url = None

        parsed_url = urlparse(card_url)
        provider_url = (
            f"{parsed_url.scheme}://{parsed_url.netloc}" if parsed_url.netloc else ""
        )
        card_type = PreviewCard.CardTypes.link
        if self.type == self.Types.image:
            card_type = PreviewCard.CardTypes.photo
        elif self.type == self.Types.video:
            card_type = PreviewCard.CardTypes.video

        title = strip_tags(_natural_language_value(data, "name"))
        description = strip_tags(
            _natural_language_value(data, "summary")
            or _natural_language_value(data, "content")
        )
        now = timezone.now()
        card, _ = PreviewCard.objects.update_or_create(
            url=card_url,
            defaults={
                "title": title,
                "description": description,
                "card_type": card_type,
                "provider_name": parsed_url.hostname or "",
                "provider_url": provider_url,
                "image_url": icon_url or "",
                "image_width": _positive_int(icon.get("width")),
                "image_height": _positive_int(icon.get("height")),
                "fetched_at": now,
                "last_referenced_at": now,
                "state": PreviewCardStates.fetched,
            },
        )
        if self.preview_card_id != card.pk:
            type(self).objects.filter(pk=self.pk).update(preview_card=card)
            self.preview_card = card

    ### Mastodon API ###

    def to_mastodon_json(
        self,
        interactions=None,
        bookmarks=None,
        identity=None,
        include_quoted_status: bool = True,
    ):
        reply_parent = None
        domain = identity.domain.uri_domain if identity else settings.MAIN_DOMAIN
        if self.in_reply_to:
            # Load the PK and author.id explicitly to prevent a SELECT on the entire author Identity
            reply_parent = (
                Post.objects.filter(object_uri=self.in_reply_to)
                .only("pk", "author_id")
                .first()
            )
        visibility_mapping = {
            self.Visibilities.public: "public",
            self.Visibilities.unlisted: "unlisted",
            self.Visibilities.followers: "private",
            self.Visibilities.mentioned: "direct",
            self.Visibilities.local_only: "public",
        }
        language = self.language
        if self.language == "":
            language = None
        value = {
            "id": str(self.pk),
            "uri": self.object_uri,
            "created_at": format_ld_date(self.published),
            "account": self.author.to_mastodon_json(),
            "content": self.safe_content_api(),
            "language": language,
            "visibility": visibility_mapping[self.visibility],
            "sensitive": self.sensitive,
            "spoiler_text": self.summary or "",
            "media_attachments": [
                attachment.to_mastodon_json() for attachment in self.attachments.all()
            ],
            "mentions": [
                mention.to_mastodon_mention_json() for mention in self.mentions.all()
            ],
            "tags": (
                [
                    {
                        "name": tag,
                        "url": f"https://{domain}/tags/{tag}/",
                    }
                    for tag in self.hashtags
                ]
                if self.hashtags
                else []
            ),
            # Filter in the list comp rather than query because the common case is no emoji in the resultset
            # When filter is on emojis like `emojis.usable()` it causes a query that is not cached by prefetch_related
            "emojis": [
                emoji.to_mastodon_json()
                for emoji in self.emojis.all()
                if emoji.is_usable
            ],
            "reblogs_count": self.stats_with_defaults["boosts"],
            "favourites_count": self.stats_with_defaults["likes"],
            "replies_count": self.stats_with_defaults["replies"],
            "url": self.absolute_object_uri(),
            "in_reply_to_id": str(reply_parent.pk) if reply_parent else None,
            "in_reply_to_account_id": (
                str(reply_parent.author_id) if reply_parent else None
            ),
            "reblog": None,
            "quote": None,
            "poll": self.type_data.to_mastodon_json(self, identity)
            if isinstance(self.type_data, QuestionData)
            else None,
            "card": (
                self.preview_card.to_mastodon_json()
                if self.preview_card_id and self.preview_card.state == "fetched"
                else None
            ),
            "text": self.safe_content_api(),
            "edited_at": format_ld_date(self.edited) if self.edited else None,
            "application": self.application.to_mastodon_status_json()
            if self.application
            else None,
        }
        if self.quote_url and include_quoted_status:
            quoted_post = (
                Post.objects.filter(object_uri=self.quote_url)
                .select_related("author")
                .first()
            )
            if quoted_post:
                value["quote"] = {
                    "state": "accepted",
                    "quoted_status": quoted_post.to_mastodon_json(
                        identity=identity, include_quoted_status=False
                    ),
                }
                value["quote_id"] = str(quoted_post.pk)
                value["quoted_status_id"] = str(quoted_post.pk)
        if isinstance(self.type_data, dict) and "object" in self.type_data:
            value["ext_neodb"] = self.type_data["object"]
        if interactions:
            value["favourited"] = self.pk in interactions.get("like", [])
            value["reblogged"] = self.pk in interactions.get("boost", [])
            value["pinned"] = self.pk in interactions.get("pin", [])
        if bookmarks:
            value["bookmarked"] = self.pk in bookmarks
        return value


def post_created(sender, instance: Post, created, **kwargs):
    if created:
        instance.author.calculate_stats()


def post_deleted(sender, instance: Post, **kwargs):
    instance.author.calculate_stats()
    # Hard deletes (incoming AP Delete, prune, admin) bypass handle_deleted;
    # skip soft-deleted posts whose parent was already recalculated there.
    if instance.state != PostStates.deleted_fanned_out:
        instance.recalculate_parent_stats()


post_save.connect(post_created, sender=Post, dispatch_uid="activities.post.created")
post_delete.connect(post_deleted, sender=Post, dispatch_uid="activities.post.deleted")
