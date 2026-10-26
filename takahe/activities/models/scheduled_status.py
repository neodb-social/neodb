import datetime
from typing import Any

from django.db import models
from django.db.models.functions import Greatest
from django.utils import timezone

from activities.models.post import Post
from activities.models.post_attachment import PostAttachment
from activities.models.post_types import poll_question
from activities.models.timeline_event import TimelineEvent
from core.ld import format_ld_date
from stator.models import State, StateField, StateGraph, StatorModel
from users.models.identity import Identity

# Client API visibility names. Local-only has no Mastodon name, so a
# scheduled local-only post stores "public" plus a local_only flag.
API_VISIBILITIES: dict[str, int] = {
    "public": Post.Visibilities.public,
    "unlisted": Post.Visibilities.unlisted,
    "private": Post.Visibilities.followers,
    "direct": Post.Visibilities.mentioned,
}


class ScheduledStatusError(ValueError):
    pass


def parse_scheduled_at(value: str) -> datetime.datetime:
    """
    Parses an ISO 8601 date or datetime; a time without an offset is UTC.
    """
    try:
        scheduled_at = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise ScheduledStatusError("Validation failed: Scheduled at is invalid")
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=datetime.UTC)
    return scheduled_at


def check_backdate(
    author: Identity,
    published: datetime.datetime,
    text: str,
    reply_to: Post | None = None,
    quote: Post | None = None,
    poll: object | None = None,
) -> None:
    """
    Raises ScheduledStatusError unless a post can be backdated to published.
    A backdated post skips federation and notifications, so it must not
    involve anyone else: no mentions, replies or quotes of others, no poll.
    """
    if published <= datetime.datetime.fromtimestamp(0, datetime.UTC):
        raise ScheduledStatusError(
            "Validation failed: Posts can't be backdated to or before the Unix epoch"
        )
    if poll:
        raise ScheduledStatusError(
            "Validation failed: Posts with polls can't be backdated"
        )
    if quote:
        raise ScheduledStatusError("Validation failed: Quote posts can't be backdated")
    if reply_to and reply_to.author_id != author.pk:
        raise ScheduledStatusError(
            "Validation failed: Replies to others can't be backdated"
        )
    if any(m != author for m in Post.mentions_from_content(text, author)):
        raise ScheduledStatusError(
            "Validation failed: Posts mentioning others can't be backdated"
        )


class ScheduledStatusStates(StateGraph):
    scheduled = State(try_interval=60)
    published = State()

    scheduled.transitions_to(published)

    @classmethod
    def handle_scheduled(cls, instance: "ScheduledStatus"):
        if instance.scheduled_at > timezone.now() or not instance.claim():
            return None
        instance.create_post()
        # The row is gone, so this transition updates nothing
        return cls.published


class ScheduledStatus(StatorModel):
    """
    A local status that stator posts at scheduled_at. params keeps the
    Mastodon ScheduledStatus params shape, so the API returns it as is.
    """

    TOTAL_LIMIT = 300
    DAILY_LIMIT = 25
    MINIMUM_OFFSET = datetime.timedelta(minutes=5)

    author = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="scheduled_statuses",
    )
    application = models.ForeignKey(
        "api.Application",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="scheduled_statuses",
    )
    scheduled_at = models.DateTimeField()
    params = models.JSONField(default=dict)

    state = StateField(ScheduledStatusStates)

    created = models.DateTimeField(auto_now_add=True)

    @classmethod
    def check_time(
        cls,
        author: Identity,
        scheduled_at: datetime.datetime,
        exclude_pk: int | None = None,
    ) -> None:
        """
        Raises ScheduledStatusError when scheduled_at is too soon or the
        author has no room left, with Mastodon's messages.
        """
        if scheduled_at <= timezone.now() + cls.MINIMUM_OFFSET:
            raise ScheduledStatusError(
                "Validation failed: Scheduled at date must be in the future"
            )
        pending = cls.objects.filter(
            author=author, state=ScheduledStatusStates.scheduled
        ).exclude(pk=exclude_pk)
        if pending.count() >= cls.TOTAL_LIMIT:
            raise ScheduledStatusError(
                f"Validation failed: You have exceeded the limit of {cls.TOTAL_LIMIT} scheduled posts"
            )
        day = scheduled_at.astimezone(datetime.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if (
            pending.filter(
                scheduled_at__gte=day,
                scheduled_at__lt=day + datetime.timedelta(days=1),
            ).count()
            >= cls.DAILY_LIMIT
        ):
            raise ScheduledStatusError(
                f"Validation failed: You have exceeded the limit of {cls.DAILY_LIMIT} scheduled posts for today"
            )

    @classmethod
    def create(
        cls,
        author: Identity,
        scheduled_at: datetime.datetime,
        text: str,
        visibility: int = Post.Visibilities.public,
        spoiler_text: str | None = None,
        sensitive: bool = False,
        language: str | None = None,
        media_ids: list[str] | None = None,
        poll: dict | None = None,
        in_reply_to_id: str | None = None,
        quoted_status_id: str | None = None,
        application=None,
    ) -> "ScheduledStatus":
        """
        Stores an already validated status. Call check_time first.
        """
        api_visibility = next(
            (k for k, v in API_VISIBILITIES.items() if v == visibility), "public"
        )
        params: dict[str, Any] = {
            "text": text,
            "poll": poll,
            "media_ids": media_ids or None,
            "sensitive": sensitive,
            "spoiler_text": spoiler_text or None,
            "visibility": api_visibility,
            "language": language or None,
            "in_reply_to_id": int(in_reply_to_id) if in_reply_to_id else None,
            "quoted_status_id": quoted_status_id or None,
            "application_id": application.pk if application else None,
            "scheduled_at": None,
            "idempotency": None,
            "with_rate_limit": False,
        }
        if visibility == Post.Visibilities.local_only:
            params["local_only"] = True
        return cls.objects.create(
            author=author,
            application=application,
            scheduled_at=scheduled_at,
            params=params,
            # Stator claims the row only once it is due
            state_next_attempt=scheduled_at,
        )

    def reschedule(self, scheduled_at: datetime.datetime) -> bool:
        """
        Moves a pending status to scheduled_at. Returns False when it has
        already gone, posted or deleted.
        """
        updated = ScheduledStatus.objects.filter(
            pk=self.pk, state=ScheduledStatusStates.scheduled
        ).update(scheduled_at=scheduled_at, state_next_attempt=scheduled_at)
        self.scheduled_at = scheduled_at
        self.state_next_attempt = scheduled_at
        return bool(updated)

    def transition_attempt(self) -> State | None:
        result = super().transition_attempt()
        if result is None:
            # Stator retries at try_interval; a row that was rescheduled
            # while queued should wait for its new time instead
            ScheduledStatus.objects.filter(
                pk=self.pk, state=ScheduledStatusStates.scheduled
            ).update(
                state_next_attempt=Greatest(
                    models.F("scheduled_at"),
                    timezone.now()
                    + datetime.timedelta(
                        seconds=ScheduledStatusStates.scheduled.try_interval or 0
                    ),
                )
            )
        return result

    @property
    def visibility(self) -> int:
        if self.params.get("local_only"):
            return Post.Visibilities.local_only
        return API_VISIBILITIES.get(
            self.params.get("visibility") or "public", Post.Visibilities.public
        )

    @classmethod
    def reserved_media_ids(cls, author: Identity) -> set[int]:
        """
        Media held unattached by the author's pending statuses. Another
        status that took it would leave the scheduled one without it.
        """
        return {
            int(i)
            for media_ids in cls.objects.filter(
                author=author, state=ScheduledStatusStates.scheduled
            ).values_list("params__media_ids", flat=True)
            for i in media_ids or []
        }

    def attachments(self) -> list[PostAttachment]:
        """
        The media still available to this status, in the requested order.
        """
        ids = [int(i) for i in self.params.get("media_ids") or []]
        found = {
            a.pk: a
            for a in PostAttachment.objects.filter(
                pk__in=ids, author=self.author, post__isnull=True
            )
        }
        return [found[i] for i in ids if i in found]

    def claim(self) -> bool:
        """
        Deletes the row if it is still due, so only one worker posts it.
        The check reads the stored row: it may have been rescheduled since
        stator loaded it. Deleting first, as Mastodon does, means a failure
        loses this status rather than posting it twice.
        """
        return bool(
            ScheduledStatus.objects.filter(
                pk=self.pk,
                state=ScheduledStatusStates.scheduled,
                scheduled_at__lte=timezone.now(),
            ).delete()[0]
        )

    def publish(self) -> Post | None:
        if not self.claim():
            return None
        return self.create_post()

    def create_post(self) -> Post | None:
        """
        Posts a claimed status. Returns None when it can no longer be posted.
        """
        author = self.author
        if author.deleted or author.blocked:
            return None
        visible = Post.objects.not_hidden().visible_to(author, include_replies=True)
        reply_to = quote = None
        if self.params.get("in_reply_to_id"):
            # Same lookup as post_for_id when the status was scheduled
            reply_to = (
                Post.objects.not_hidden()
                .visible_to(author, include_replies=True, include_muted=True)
                .filter(pk=self.params["in_reply_to_id"])
                .first()
            )
            if reply_to is None:
                return None
        if self.params.get("quoted_status_id"):
            quote = visible.filter(pk=self.params["quoted_status_id"]).first()
            if quote is None:
                return None
        text = self.params.get("text") or ""
        attachments = self.attachments()
        if not text and not attachments:
            return None
        visibility = self.visibility
        if quote:
            visibility = Post.clamp_quote_visibility(visibility, quote.visibility)
        poll = self.params.get("poll")
        post = Post.create_local(
            author=author,
            content=text,
            summary=self.params.get("spoiler_text"),
            sensitive=bool(self.params.get("sensitive")),
            visibility=visibility,
            reply_to=reply_to,
            quote=quote,
            attachments=attachments,
            question=poll_question(**poll) if poll else None,
            language=self.params.get("language"),
            application=self.application,
        )
        TimelineEvent.add_post(author, post)
        return post

    def to_mastodon_json(self) -> dict:
        return {
            "id": str(self.pk),
            "scheduled_at": format_ld_date(self.scheduled_at),
            "params": self.params,
            "media_attachments": [a.to_mastodon_json() for a in self.attachments()],
        }
