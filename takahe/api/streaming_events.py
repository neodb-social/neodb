"""Publish committed changes for the Mastodon streaming API."""

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from redis import Redis, RedisError
from redis.backoff import NoBackoff
from redis.retry import Retry


if TYPE_CHECKING:
    from activities.models import Post, TimelineEvent

logger = logging.getLogger(__name__)


def streaming_enabled() -> bool:
    return settings.SETUP.STREAMING_ENABLED


def redis_url() -> str:
    return str(settings.SETUP.CACHES_DEFAULT or "redis://localhost")


def channel(name: str) -> str:
    return f"takahe:streaming:{settings.SETUP.MAIN_DOMAIN}:{name}"


@lru_cache(maxsize=4)
def publisher(url: str) -> Redis:
    return Redis.from_url(
        url, socket_connect_timeout=2, socket_timeout=2, retry=Retry(NoBackoff(), 0)
    )


def publish_many(messages: list[tuple[str, dict]]) -> None:
    if not streaming_enabled():
        return
    payloads = [(channel(name), json.dumps(message)) for name, message in messages]

    def send() -> None:
        if not streaming_enabled():
            return
        try:
            redis = publisher(redis_url())
            if len(payloads) == 1:
                redis.publish(*payloads[0])
            elif payloads:
                with redis.pipeline(transaction=False) as pipeline:
                    for name, payload in payloads:
                        pipeline.publish(name, payload)
                    pipeline.execute()
        except RedisError:
            # Streaming is best effort; a disconnected client reloads via REST.
            logger.warning("Unable to publish streaming events", exc_info=True)

    transaction.on_commit(send)


def publish(name: str, message: dict) -> None:
    publish_many([(name, message)])


def publish_post(post: "Post", event: str) -> None:
    if not streaming_enabled():
        return
    message = {"kind": "post", "id": post.pk, "event": event}
    messages = [("posts", message)]
    if event == "delete":
        parent = post.in_reply_to_post() if post.in_reply_to else None
        message.update(
            author=post.author_id,
            domain=post.author.domain_id,
            local=post.local,
            visibility=post.visibility,
            tags=post.hashtags or [],
            media=post.attachments.exists(),
            reply=bool(post.in_reply_to),
            reply_author=parent.author_id if parent else None,
            mentions=list(post.mentions.values_list("pk", flat=True)),
        )
    if event in {"status.update", "delete"}:
        recipients: dict[int, set[str]] = {}
        for identity_id, boost_id in (
            post.timeline_events.filter(
                identity__local=True, type__in=["post", "boost"]
            )
            .values_list("identity_id", "subject_post_interaction_id")
            .iterator()
        ):
            ids = recipients.setdefault(identity_id, {str(post.pk)})
            if boost_id:
                ids.add(str(boost_id))
        if post.local:
            recipients.setdefault(post.author_id, {str(post.pk)})
        for identity_id, ids in recipients.items():
            payload = (
                {"kind": "delete", "ids": sorted(ids)}
                if event == "delete"
                else {"kind": "user_post", "id": post.pk, "event": event}
            )
            messages.append((f"user:{identity_id}", payload))
    elif event == "update" and post.local:
        # NeoDB writes the author's timeline row through its mirror model, so
        # publish here for both apps instead of relying on its post_save signal.
        messages.append(
            (
                f"user:{post.author_id}",
                {
                    "kind": "user_post",
                    "id": post.pk,
                    "event": event,
                },
            )
        )
    if event != "delete" and post.conversation_id:
        # Stator also processes direct messages written by NeoDB's mirror models.
        messages.extend(
            (
                f"user:{identity_id}",
                {"kind": "conversation", "id": post.conversation_id},
            )
            for identity_id in post.conversation.participants.filter(
                local=True
            ).values_list("pk", flat=True)
        )
    publish_many(messages)


def timeline_created(
    sender: type, instance: "TimelineEvent", created: bool, raw: bool = False, **kwargs
) -> None:
    if created and not raw:
        if (
            instance.type == "post"
            and instance.identity_id == instance.subject_identity_id
        ):
            return
        publish(
            f"user:{instance.identity_id}",
            {"kind": "timeline", "id": instance.pk},
        )


def connect_signals() -> None:
    post_save.connect(
        timeline_created,
        sender="activities.TimelineEvent",
        dispatch_uid="streaming.timeline_created",
    )
