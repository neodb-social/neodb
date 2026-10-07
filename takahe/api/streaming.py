"""Mastodon SSE and multiplexed WebSocket transport, served directly by ASGI."""

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import DatabaseError, close_old_connections, reset_queries
from django.db.models import Q, QuerySet
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from activities.models import Post, PostInteraction, TimelineEvent
from activities.services import PostService, TimelineService
from api import schemas
from api.models import Token
from api.streaming_events import channel, redis_url, streaming_enabled
from core.models import Config
from users.models import Block, List

logger = logging.getLogger(__name__)
_config_lock = threading.Lock()
_config_loaded_at = 0.0
_database_executor = ThreadPoolExecutor(
    max_workers=settings.SETUP.STREAMING_DB_THREADS,
    thread_name_prefix="streaming-db",
)

PREFIX = "/api/v1/streaming"
HEARTBEAT = 15
MAX_SUBSCRIPTIONS = 100
STREAMS = {
    "user",
    "user:notification",
    "direct",
    "list",
    "hashtag",
    "hashtag:local",
    "public",
    "public:local",
    "public:remote",
    "public:media",
    "public:local:media",
    "public:remote:media",
}


class StreamError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        self.status = status
        super().__init__(message)


def database_sync_to_async(function: Callable) -> Callable:
    def run(*args: Any, **kwargs: Any) -> Any:
        close_old_connections()
        reset_queries()
        try:
            return function(*args, **kwargs)
        finally:
            close_old_connections()

    return sync_to_async(run, thread_sensitive=False, executor=_database_executor)


def authenticate(value: str) -> Token:
    if not streaming_enabled():
        raise StreamError("Streaming disabled", 404)
    token = (
        Token.objects.select_related("identity", "identity__domain", "user")
        .filter(token=value, revoked=None, identity__local=True, identity__deleted=None)
        .first()
    )
    if token is None or token.user is None or not token.user.is_active:
        raise StreamError("Invalid access token", 401)
    if not (token.has_scope("read:statuses") or token.has_scope("read:notifications")):
        raise StreamError("Insufficient scope", 403)
    return token


def refresh_config() -> None:
    global _config_loaded_at
    if getattr(Config, "__forced__", False):
        return
    with _config_lock:
        if (
            not getattr(Config, "system", None)
            or time.monotonic() - _config_loaded_at >= 5
        ):
            Config.system = Config.load_system()
            _config_loaded_at = time.monotonic()


@dataclass(frozen=True)
class Subscription:
    stream: str
    argument: str = ""

    @property
    def label(self) -> list[str]:
        return [self.stream, self.argument] if self.argument else [self.stream]

    @classmethod
    def parse(
        cls, params: dict, token: Token, *, check_access: bool = True
    ) -> "Subscription":
        stream = params.get("stream")
        if not isinstance(stream, str) or stream not in STREAMS:
            raise StreamError("Unknown stream type")
        if check_access and not cls(stream).allowed(token):
            raise StreamError("Insufficient scope", 403)
        argument = ""
        if stream == "list":
            argument = params.get("list", "")
            if (
                not isinstance(argument, str)
                or not argument.isdecimal()
                or len(argument) > 19
                or int(argument) > 2**63 - 1
            ):
                raise StreamError("List not found", 404)
            if (
                check_access
                and not List.objects.filter(
                    pk=argument, identity=token.identity
                ).exists()
            ):
                raise StreamError("List not found", 404)
        elif stream.startswith("hashtag"):
            argument = params.get("tag", "")
            if (
                not isinstance(argument, str)
                or not argument.strip()
                or len(argument) > 200
            ):
                raise StreamError("Missing or invalid tag")
            argument = argument.lower().lstrip("#")
            if not argument:
                raise StreamError("Missing or invalid tag")
        return cls(stream, argument)

    @property
    def needs_posts(self) -> bool:
        return self.stream not in {"user", "user:notification", "direct"}

    def allowed(self, token: Token) -> bool:
        if self.stream == "user":
            return token.has_scope("read:statuses") or token.has_scope(
                "read:notifications"
            )
        scope = (
            "read:notifications"
            if self.stream == "user:notification"
            else "read:statuses"
        )
        return token.has_scope(scope)

    def accepts(self, message: dict) -> bool:
        kind = message.get("kind")
        if kind == "post":
            return self.needs_posts
        if kind in {"user_post", "delete"}:
            return self.stream == "user"
        if kind == "timeline":
            return self.stream in {"user", "user:notification"}
        if kind == "conversation":
            return self.stream in {"user", "direct"}
        return False

    def posts(self, token: Token) -> QuerySet[Post]:
        service = TimelineService(token.identity)
        if self.stream == "list":
            alist = token.identity.lists.filter(pk=self.argument).first()
            return service.for_list(alist) if alist else Post.objects.none()
        if self.stream.startswith("hashtag"):
            posts = service.hashtag(self.argument)
            if self.stream.endswith(":local"):
                posts = posts.filter(local=True)
            return posts
        if self.stream.startswith("public"):
            parts = self.stream.split(":")
            posts = service.local() if "local" in parts else service.federated()
            if "remote" in parts:
                posts = posts.filter(local=False)
            if "media" in parts:
                posts = posts.filter(attachments__isnull=False)
            return posts
        return Post.objects.none()

    def receives_delete(self, message: dict, token: Token) -> bool:
        identity = token.identity
        public = message["visibility"] in (
            Post.Visibilities.public,
            Post.Visibilities.local_only,
        )
        if self.stream == "list":
            alist = identity.lists.filter(pk=self.argument).first()
            if alist is None or (message["reply"] and alist.replies_policy == "none"):
                return False
            member = alist.members.filter(pk=message["author"]).exists()
            if not member and not (
                alist.replies_policy == "followed"
                and message.get("reply_author")
                and alist.members.filter(pk=message["reply_author"]).exists()
                and identity.outbound_follows.active()
                .filter(target_id=message["author"])
                .exists()
            ):
                return False
            return (
                public
                or message["visibility"] == Post.Visibilities.unlisted
                or message["author"] == identity.pk
                or identity.pk in message["mentions"]
                or (
                    message["visibility"] == Post.Visibilities.followers
                    and identity.outbound_follows.active()
                    .filter(target_id=message["author"])
                    .exists()
                )
            )
        if not public or message["reply"]:
            return False
        if self.stream.startswith("public"):
            parts = self.stream.split(":")
            return (
                (
                    "local" not in parts
                    or (message["local"] and message["domain"] == identity.domain_id)
                )
                and ("remote" not in parts or not message["local"])
                and ("media" not in parts or message["media"])
            )
        return (
            self.stream.startswith("hashtag")
            and self.argument in message["tags"]
            and (not self.stream.endswith(":local") or message["local"])
        )


def render_events(
    token_value: str, subscriptions: set[Subscription], message: dict
) -> list[dict]:
    """Recheck authorization and render with the recipient's current relationships."""
    subscriptions = {sub for sub in subscriptions if sub.accepts(message)}
    if not subscriptions:
        return []
    token = authenticate(token_value)
    refresh_config()
    identity = token.identity
    service = TimelineService(identity)
    output = []
    post_payloads: dict[int, str] = {}

    def add(subscription: Subscription, event: str, payload: str) -> None:
        output.append(
            {"stream": subscription.label, "event": event, "payload": payload}
        )

    for sub in subscriptions:
        if not sub.allowed(token):
            continue
        if message["kind"] == "delete":
            if sub.stream == "user" and token.has_scope("read:statuses"):
                for status_id in message.get("ids", [message.get("id")]):
                    add(sub, "delete", str(status_id))
        elif message["kind"] == "timeline":
            if sub.stream == "user" and token.has_scope("read:statuses"):
                event = service.home().filter(pk=message["id"]).first()
                if (
                    event
                    and PostService.queryset()
                    .visible_to(identity, include_replies=True)
                    .filter(pk=event.subject_post_id)
                    .exists()
                ):
                    status = schemas.Status.map_from_timeline_event([event], identity)[
                        0
                    ]
                    add(sub, "update", status.model_dump_json())
            if sub.stream in {"user", "user:notification"} and token.has_scope(
                "read:notifications"
            ):
                event = (
                    service.notifications(list(TimelineEvent.NOTIFICATION_NAMES))
                    .filter(pk=message["id"])
                    .first()
                )
                if event:
                    if (
                        Block.objects.active()
                        .filter(
                            (
                                Q(source=identity, target_id=event.subject_identity_id)
                                & (Q(mute=False) | Q(include_notifications=True))
                            )
                            | Q(
                                source_id=event.subject_identity_id,
                                target=identity,
                                mute=False,
                            )
                        )
                        .exists()
                    ):
                        continue
                    if (
                        event.subject_post_id
                        and not PostService.queryset()
                        .visible_to(identity, include_replies=True, include_muted=True)
                        .filter(pk=event.subject_post_id)
                        .exists()
                    ):
                        continue
                    interactions = PostInteraction.get_event_interactions(
                        [event], identity
                    )
                    notification = schemas.Notification.from_timeline_event(
                        event, interactions
                    )
                    add(sub, "notification", notification.model_dump_json())
        elif message["kind"] == "conversation":
            if sub.stream in {"user", "direct"} and token.has_scope("read:statuses"):
                conversation = service.conversations().filter(pk=message["id"]).first()
                if conversation and (
                    not conversation.last_post_id
                    or PostService.queryset()
                    .visible_to(identity, include_replies=True)
                    .filter(pk=conversation.last_post_id)
                    .exists()
                ):
                    payload = schemas.Conversation.from_conversation(
                        conversation, identity
                    )
                    add(sub, "conversation", payload.model_dump_json())
        elif message["kind"] in {"post", "user_post"}:
            if message["event"] == "delete":
                if sub.receives_delete(message, token):
                    add(sub, "delete", str(message["id"]))
                continue
            if message["kind"] == "user_post":
                if not token.has_scope("read:statuses"):
                    continue
                posts = PostService.queryset().visible_to(
                    identity, include_replies=True
                )
                if message["event"] == "update":
                    posts = posts.filter(author=identity)
                    if identity.lists.filter(exclusive=True, members=identity).exists():
                        continue
                elif not service.home().filter(subject_post_id=message["id"]).exists():
                    continue
            else:
                posts = sub.posts(token)
            if message["id"] in post_payloads:
                if posts.filter(pk=message["id"]).exists():
                    add(sub, message["event"], post_payloads[message["id"]])
                continue
            post = posts.filter(pk=message["id"]).first()
            if post:
                status = schemas.Status.map_from_post([post], identity)[0]
                post_payloads[post.pk] = status.model_dump_json()
                add(sub, message["event"], post_payloads[post.pk])
    return output


class StreamingApplication:
    def __init__(self, application: Callable | None = None) -> None:
        self.application = application

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        with contextlib.suppress(OSError):
            await self.handle(scope, receive, send)

    async def handle(self, scope: dict, receive: Callable, send: Callable) -> None:
        path = scope.get("path", "").rstrip("/")
        if not (path == PREFIX or path.startswith(PREFIX + "/")):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif self.application is not None:
                await self.application(scope, receive, send)
            else:
                await self.response(send, 404, b'{"error":"Not found"}')
            return
        websocket = scope["type"] == "websocket"
        if websocket:
            if (await receive())["type"] != "websocket.connect":
                return
        elif scope.get("method") == "OPTIONS":
            await self.response(send, 204, b"")
            return
        elif scope.get("method") != "GET":
            await self.response(send, 405, b'{"error":"Method not allowed"}')
            return
        if path == PREFIX + "/health" and not websocket:
            await self.response(send, 200, b"OK", b"text/plain")
            return
        if not streaming_enabled():
            await self.reject(send, websocket, StreamError("Streaming disabled", 404))
            return
        params = dict(
            parse_qsl(scope.get("query_string", b"").decode("utf-8", errors="replace"))
        )
        if path == PREFIX and not websocket and "stream" not in params:
            await self.response(send, 404, b'{"error":"Not found"}')
            return
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin1")
        protocols = scope.get("subprotocols", [])
        value = params.get("access_token", "")
        selected_protocol = None
        if authorization:
            value = (
                authorization[7:] if authorization.lower().startswith("bearer ") else ""
            )
        elif not value and websocket and protocols:
            value = selected_protocol = protocols[0]
        if path != PREFIX:
            params["stream"] = path[len(PREFIX) + 1 :].replace("/", ":")
            if params.get("only_media") in {"true", "1"} and params[
                "stream"
            ].startswith("public"):
                params["stream"] += ":media"
        try:
            token = await database_sync_to_async(authenticate)(value)
            subscriptions = set()
            if "stream" in params or not websocket:
                subscriptions.add(
                    await database_sync_to_async(Subscription.parse)(params, token)
                )
        except StreamError as exc:
            await self.reject(send, websocket, exc)
            return
        except DatabaseError:
            await self.reject(
                send, websocket, StreamError("Streaming unavailable", 503)
            )
            return
        async with Redis.from_url(
            redis_url(),
            socket_connect_timeout=5,
            socket_keepalive=True,
            retry=Retry(NoBackoff(), 0),
        ) as redis:
            async with redis.pubsub() as pubsub:
                pending: list[dict] = []
                try:
                    channels = self.channels(subscriptions, token.identity_id)
                    # Confirm the subscriptions before accepting the client.
                    async with asyncio.timeout(5):
                        await pubsub.subscribe(*channels)
                        remaining = len(channels)
                        while remaining:
                            message = await pubsub.get_message(timeout=5)
                            if message and message["type"] == "subscribe":
                                remaining -= 1
                            elif message and message["type"] == "message":
                                pending.append(message)
                except RedisError, TimeoutError:
                    await self.reject(
                        send, websocket, StreamError("Streaming unavailable", 503)
                    )
                    return
                if websocket:
                    await send(
                        {"type": "websocket.accept", "subprotocol": selected_protocol}
                    )
                else:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                (b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-store"),
                                (b"x-accel-buffering", b"no"),
                                (b"access-control-allow-origin", b"*"),
                            ],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b":connected\n\n",
                            "more_body": True,
                        }
                    )

                async def next_message() -> dict | None:
                    if pending:
                        return pending.pop(0)
                    return await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=HEARTBEAT
                    )

                listener = asyncio.create_task(receive())
                reader = asyncio.create_task(next_message())
                heartbeat = asyncio.create_task(asyncio.sleep(HEARTBEAT))
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {listener, reader, heartbeat},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if heartbeat in done:
                            await database_sync_to_async(authenticate)(value)
                            if not websocket:
                                await send(
                                    {
                                        "type": "http.response.body",
                                        "body": b":thump\n\n",
                                        "more_body": True,
                                    }
                                )
                            heartbeat = asyncio.create_task(asyncio.sleep(HEARTBEAT))
                        if listener in done:
                            incoming = listener.result()
                            if incoming["type"] in {
                                "websocket.disconnect",
                                "http.disconnect",
                            }:
                                return
                            if websocket:
                                try:
                                    command = json.loads(incoming.get("text") or "")
                                    if not isinstance(command, dict) or command.get(
                                        "type"
                                    ) not in ("subscribe", "unsubscribe"):
                                        raise StreamError(
                                            "Invalid subscription command"
                                        )
                                    token = await database_sync_to_async(authenticate)(
                                        value
                                    )
                                    sub = await database_sync_to_async(
                                        Subscription.parse
                                    )(
                                        command,
                                        token,
                                        check_access=command["type"] == "subscribe",
                                    )
                                    if command["type"] == "subscribe":
                                        if (
                                            len(subscriptions) >= MAX_SUBSCRIPTIONS
                                            and sub not in subscriptions
                                        ):
                                            raise StreamError("Too many subscriptions")
                                        subscriptions.add(sub)
                                    else:
                                        subscriptions.discard(sub)
                                    updated_channels = self.channels(
                                        subscriptions, token.identity_id
                                    )
                                    if added := updated_channels - channels:
                                        await pubsub.subscribe(*added)
                                    if removed := channels - updated_channels:
                                        await pubsub.unsubscribe(*removed)
                                    channels = updated_channels
                                except (ValueError, StreamError) as exc:
                                    await send(
                                        {
                                            "type": "websocket.send",
                                            "text": json.dumps(
                                                {
                                                    "error": str(exc),
                                                    "status": getattr(
                                                        exc, "status", 400
                                                    ),
                                                }
                                            ),
                                        }
                                    )
                            listener = asyncio.create_task(receive())
                        if reader in done:
                            message = reader.result()
                            if message:
                                try:
                                    payload = json.loads(message["data"])
                                    if any(
                                        sub.accepts(payload) for sub in subscriptions
                                    ):
                                        events = await database_sync_to_async(
                                            render_events
                                        )(value, subscriptions, payload)
                                    else:
                                        events = []
                                except StreamError:
                                    raise
                                except Exception:
                                    logger.exception("Unable to render streaming event")
                                    events = []
                                for event in events:
                                    if websocket:
                                        await send(
                                            {
                                                "type": "websocket.send",
                                                "text": json.dumps(event),
                                            }
                                        )
                                    else:
                                        body = f"event: {event['event']}\ndata: {event['payload']}\n\n".encode()
                                        await send(
                                            {
                                                "type": "http.response.body",
                                                "body": body,
                                                "more_body": True,
                                            }
                                        )
                            reader = asyncio.create_task(next_message())
                except StreamError, RedisError, DatabaseError:
                    if websocket:
                        await send({"type": "websocket.close", "code": 1011})
                    else:
                        await send(
                            {
                                "type": "http.response.body",
                                "body": b"",
                                "more_body": False,
                            }
                        )
                finally:
                    for task in (listener, reader, heartbeat):
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, RedisError):
                            await task

    @staticmethod
    def channels(subscriptions: set[Subscription], identity_id: int) -> set[str]:
        channels = set()
        if not subscriptions or any(not sub.needs_posts for sub in subscriptions):
            channels.add(channel(f"user:{identity_id}"))
        if any(sub.needs_posts for sub in subscriptions):
            channels.add(channel("posts"))
        return channels

    @staticmethod
    async def response(
        send: Callable,
        status: int,
        body: bytes,
        content_type: bytes = b"application/json",
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-headers", b"Authorization, Content-Type"),
                    (b"access-control-allow-methods", b"GET, OPTIONS"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def reject(self, send: Callable, websocket: bool, error: StreamError) -> None:
        if websocket:
            await send({"type": "websocket.close", "code": 1008})
        else:
            await self.response(
                send, error.status, json.dumps({"error": str(error)}).encode()
            )
