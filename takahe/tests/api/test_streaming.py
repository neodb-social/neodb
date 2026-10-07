import asyncio
import json
import threading
from datetime import timedelta
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.conf import settings
from django.db import OperationalError, transaction
from django.utils import timezone
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError

from activities.models import (
    Post,
    PostAttachment,
    PostInteraction,
    PostStates,
    TimelineEvent,
)
from activities.models.conversation import Conversation, ConversationMembership
from api import schemas
from api.models import Token
from api.streaming import (
    PREFIX,
    StreamError,
    StreamingApplication,
    Subscription,
    authenticate,
    database_sync_to_async,
    render_events,
    refresh_config,
)
from api.streaming_events import channel, publish, publish_post, publisher, redis_url
from core.models import Config
from takahe.asgi import application as streaming_application
from users.models import Block, List


@pytest.fixture(autouse=True)
def enable_streaming(settings, monkeypatch) -> None:
    monkeypatch.setattr(settings.SETUP, "STREAMING_ENABLED", True)


async def fallback(scope: dict, receive, send) -> None:
    await send({"type": "http.response.start", "status": 404, "headers": []})
    await send({"type": "http.response.body", "body": b"fallback"})


def connection(
    path: str = PREFIX, query: str = "", websocket: bool = True, **kwargs
) -> ApplicationCommunicator:
    return ApplicationCommunicator(
        StreamingApplication(fallback),
        {
            "type": "websocket" if websocket else "http",
            "path": path,
            "method": "GET",
            "query_string": query.encode(),
            "headers": [],
            **kwargs,
        },
    )


async def disconnect(client: ApplicationCommunicator, websocket: bool = True) -> None:
    await client.send_input(
        {
            "type": "websocket.disconnect" if websocket else "http.disconnect",
            "code": 1000,
        }
    )
    await client.wait()


def make_post(identity, **kwargs) -> Post:
    post = Post.objects.create(
        author=identity,
        local=identity.local,
        content="<p>Hello</p>",
        published=timezone.now(),
        **kwargs,
    )
    post.object_uri = f"https://example.com/posts/{post.pk}"
    post.save()
    return post


def render(token: Token, stream: str, message: dict, argument: str = "") -> list[dict]:
    return render_events(token.token, {Subscription(stream, argument)}, message)


@pytest.mark.django_db
def test_authentication(api_token, identity) -> None:
    assert authenticate(api_token.token).pk == api_token.pk
    for value in ("", "invalid"):
        with pytest.raises(StreamError, match="Invalid access token"):
            authenticate(value)
    api_token.scopes = ["write"]
    api_token.save()
    with pytest.raises(StreamError, match="Insufficient scope"):
        authenticate(api_token.token)
    api_token.scopes = ["read"]
    api_token.revoked = timezone.now()
    api_token.save()
    with pytest.raises(StreamError, match="Invalid access token"):
        authenticate(api_token.token)
    api_token.revoked = None
    api_token.identity = None
    api_token.save()
    with pytest.raises(StreamError, match="Invalid access token"):
        authenticate(api_token.token)
    api_token.identity = identity
    api_token.save()
    api_token.user.banned = True
    api_token.user.save()
    with pytest.raises(StreamError, match="Invalid access token"):
        authenticate(api_token.token)


@pytest.mark.django_db
def test_subscription_validation(api_token, identity, other_identity) -> None:
    alist = List.objects.create(
        identity=identity, title="Mine", replies_policy="list", exclusive=False
    )
    other = List.objects.create(
        identity=other_identity, title="Other", replies_policy="list", exclusive=False
    )
    assert Subscription.parse(
        {"stream": "list", "list": str(alist.pk)}, api_token
    ).label == ["list", str(alist.pk)]
    assert Subscription.parse(
        {"stream": "hashtag", "tag": "NeoDB"}, api_token
    ).label == ["hashtag", "neodb"]
    for params in [
        {},
        {"stream": []},
        {"stream": "nope"},
        {"stream": "list", "list": str(other.pk)},
        {"stream": "list", "list": "9" * 30},
        {"stream": "list", "list": "9" * 19},
        {"stream": "list", "list": []},
        {"stream": "hashtag"},
        {"stream": "hashtag", "tag": "#"},
    ]:
        with pytest.raises(StreamError):
            Subscription.parse(params, api_token)
    api_token.scopes = ["read:notifications"]
    Subscription.parse({"stream": "user"}, api_token)
    Subscription.parse({"stream": "user:notification"}, api_token)
    with pytest.raises(StreamError):
        Subscription.parse({"stream": "public"}, api_token)


@pytest.mark.django_db
def test_timeline_payload_and_scope(api_token, identity, other_identity) -> None:
    post = make_post(other_identity)
    event = TimelineEvent.add_post(identity, post)
    message = {"kind": "timeline", "id": event.pk}
    events = render(api_token, "user", message)
    assert len(events) == 1
    assert events[0]["event"] == "update"
    assert json.loads(events[0]["payload"])["id"] == str(post.pk)
    assert render(api_token, "user:notification", message) == []
    api_token.scopes = ["read:notifications"]
    api_token.save()
    assert render(api_token, "user", message) == []


@pytest.mark.django_db
def test_notification_is_private(api_token, identity, other_identity) -> None:
    event = TimelineEvent.objects.create(
        identity=identity, type="followed", subject_identity=other_identity
    )
    message = {"kind": "timeline", "id": event.pk}
    events = render(api_token, "user:notification", message)
    assert len(events) == 1
    assert json.loads(events[0]["payload"])["type"] == "follow"
    event.identity = other_identity
    event.save()
    assert render(api_token, "user:notification", message) == []


@pytest.mark.django_db
def test_public_filtering(api_token, identity, remote_identity) -> None:
    post = make_post(remote_identity, hashtags=["neodb"])
    message = {"kind": "post", "event": "update", "id": post.pk}
    assert render(api_token, "public", message)
    assert render(api_token, "public:remote", message)
    assert not render(api_token, "public:local", message)
    assert not render(api_token, "public:media", message)
    assert render(api_token, "hashtag", message, "neodb")
    assert not render(api_token, "hashtag", message, "other")
    PostAttachment.objects.create(post=post, mimetype="image/png", name="Photo")
    assert render(api_token, "public:remote:media", message)
    post.visibility = Post.Visibilities.followers
    post.save()
    assert not render(api_token, "public", message)
    post.visibility = Post.Visibilities.public
    post.save()
    Block.objects.create(source=identity, target=remote_identity, mute=False)
    assert not render(api_token, "public", message)


@pytest.mark.django_db
def test_home_rechecks_visibility_and_exclusive_lists(
    api_token, identity, other_identity
) -> None:
    post = make_post(other_identity)
    event = TimelineEvent.add_post(identity, post)
    message = {"kind": "timeline", "id": event.pk}
    alist = List.objects.create(
        identity=identity, title="Exclusive", replies_policy="list", exclusive=True
    )
    alist.members.add(other_identity)
    assert not render(api_token, "user", message)
    assert render(
        api_token,
        "list",
        {"kind": "post", "event": "update", "id": post.pk},
        str(alist.pk),
    )
    alist.delete()
    post.visibility = Post.Visibilities.mentioned
    post.save()
    assert not render(api_token, "user", message)


@pytest.mark.django_db
def test_edits_and_deletes(
    api_token, identity, other_identity, django_capture_on_commit_callbacks
) -> None:
    post = make_post(other_identity)
    TimelineEvent.add_post(identity, post)
    message = {"kind": "post", "event": "status.update", "id": post.pk}
    assert (
        render(api_token, "user", {**message, "kind": "user_post"})[0]["event"]
        == "status.update"
    )
    assert render(api_token, "public", message)[0]["event"] == "status.update"
    with patch("api.streaming_events.publish_many") as mock:
        publish_post(post, "delete")
    deleted = mock.call_args.args[0][0][1]
    post.delete()
    assert render(api_token, "public", deleted)[0]["payload"] == str(message["id"])
    deleted["visibility"] = Post.Visibilities.mentioned
    assert not render(api_token, "public", deleted)


@pytest.mark.django_db
def test_direct_conversation(api_token, identity, other_identity) -> None:
    post = make_post(other_identity, visibility=Post.Visibilities.mentioned)
    post.mentions.add(identity)
    Conversation.update_for_post(post)
    message = {"kind": "conversation", "id": post.conversation_id}
    events = render(api_token, "direct", message)
    assert json.loads(events[0]["payload"])["last_status"]["id"] == str(post.pk)
    assert render(api_token, "user", message)
    assert not render(api_token, "public", message)
    post.conversation.memberships.filter(identity=identity).delete()
    assert not render(api_token, "direct", message)


@pytest.mark.django_db
def test_publish_after_commit_and_rollback(
    identity, django_capture_on_commit_callbacks
) -> None:
    with patch("api.streaming_events.publisher") as mock:
        with django_capture_on_commit_callbacks(execute=True):
            TimelineEvent.objects.create(
                identity=identity, type="followed", subject_identity=identity
            )
            mock.assert_not_called()
        assert mock.return_value.publish.call_count == 1
        assert mock.return_value.publish.call_args.args[0] == channel(
            f"user:{identity.pk}"
        )
        mock.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(ValueError), transaction.atomic():
                publish("posts", {"kind": "post", "event": "update", "id": 1})
                raise ValueError("rollback")
        mock.assert_not_called()


@pytest.mark.django_db
def test_post_lifecycle_publishes(identity) -> None:
    post = make_post(identity)
    with (
        patch("activities.models.post.publish_post") as publish_mock,
        patch.object(PostStates, "targets_fan_out"),
        patch("activities.models.post._attach_preview_card"),
    ):
        PostStates.handle_new(post)
        publish_mock.assert_called_with(post, "update")
        PostStates.handle_edited(post)
        publish_mock.assert_called_with(post, "status.update")
        PostStates.handle_deleted(post)
        publish_mock.assert_called_with(post, "delete")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("auth_style", ["query", "header", "protocol"])
async def test_websocket_delivery_and_disconnect(
    api_token, identity, other_identity, auth_style
) -> None:
    kwargs = {}
    query = "stream=user"
    if auth_style == "query":
        query += f"&access_token={api_token.token}"
    elif auth_style == "header":
        kwargs["headers"] = [(b"authorization", f"Bearer {api_token.token}".encode())]
    else:
        kwargs["subprotocols"] = [api_token.token]
    client = connection(query=query, **kwargs)
    await client.send_input({"type": "websocket.connect"})
    accepted = await client.receive_output(timeout=5)
    assert accepted["type"] == "websocket.accept"
    assert accepted["subprotocol"] == (
        api_token.token if auth_style == "protocol" else None
    )
    post = await sync_to_async(make_post)(other_identity)
    await sync_to_async(TimelineEvent.add_post)(identity, post)
    event = json.loads((await client.receive_output(timeout=5))["text"])
    assert event["stream"] == ["user"]
    assert event["event"] == "update"
    assert json.loads(event["payload"])["id"] == str(post.pk)
    await disconnect(client)
    counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(
        channel(f"user:{identity.pk}")
    )
    assert counts[0][1] == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_multiplex_and_invalid_commands(api_token, identity) -> None:
    client = connection(query=f"access_token={api_token.token}")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    for text in [
        "bad json",
        "[]",
        '{"type": [], "stream": "public"}',
        '{"type":"subscribe","stream":"missing"}',
    ]:
        await client.send_input({"type": "websocket.receive", "text": text})
        assert json.loads((await client.receive_output())["text"])["status"] == 400
    for stream in ("public", "hashtag"):
        await client.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": "subscribe", "stream": stream, "tag": "neodb"}
                ),
            }
        )
    # A command error is a barrier: both subscriptions have been processed.
    await client.send_input({"type": "websocket.receive", "text": "[]"})
    await client.receive_output()
    post = await sync_to_async(make_post)(identity, hashtags=["neodb"])
    await sync_to_async(publish_post)(post, "update")
    events = [
        json.loads((await client.receive_output(timeout=5))["text"]) for _ in range(2)
    ]
    assert {tuple(event["stream"]) for event in events} == {
        ("public",),
        ("hashtag", "neodb"),
    }
    await client.send_input(
        {
            "type": "websocket.receive",
            "text": '{"type":"unsubscribe","stream":"public"}',
        }
    )
    await client.send_input({"type": "websocket.receive", "text": "[]"})
    await client.receive_output()
    await sync_to_async(publish_post)(post, "status.update")
    event = json.loads((await client.receive_output(timeout=5))["text"])
    assert event["stream"] == ["hashtag", "neodb"]
    assert await client.receive_nothing()
    await client.send_input(
        {
            "type": "websocket.receive",
            "text": '{"type":"unsubscribe","stream":"hashtag","tag":"neodb"}',
        }
    )
    await client.send_input({"type": "websocket.receive", "text": "[]"})
    await client.receive_output()
    counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(channel("posts"))
    assert counts[0][1] == 0
    await disconnect(client)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["delete_list", "remove_scope"])
async def test_unsubscribe_after_losing_list_access(
    api_token, identity, change: str
) -> None:
    alist = await sync_to_async(List.objects.create)(
        identity=identity, title="Mine", replies_policy="list", exclusive=False
    )
    list_id = str(alist.pk)
    client = connection(
        query=f"access_token={api_token.token}&stream=list&list={list_id}"
    )
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    try:
        if change == "delete_list":
            await sync_to_async(alist.delete)()
        else:
            api_token.scopes = ["read:notifications"]
            await sync_to_async(api_token.save)()
        await client.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": "unsubscribe", "stream": "list", "list": list_id}
                ),
            }
        )
        await client.send_input({"type": "websocket.receive", "text": "[]"})
        assert json.loads((await client.receive_output())["text"])["status"] == 400
        counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(
            channel("posts")
        )
        assert counts[0][1] == 0
        assert await client.receive_nothing()
    finally:
        await disconnect(client)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_and_revocation(api_token, identity, other_identity) -> None:
    client = connection(
        path=PREFIX + "/user", query=f"access_token={api_token.token}", websocket=False
    )
    await client.send_input({"type": "http.request", "body": b""})
    response = await client.receive_output(timeout=5)
    assert response["status"] == 200
    assert (b"content-type", b"text/event-stream") in response["headers"]
    assert (await client.receive_output())["body"].startswith(b":")
    post = await sync_to_async(make_post)(other_identity)
    await sync_to_async(TimelineEvent.add_post)(identity, post)
    body = (await client.receive_output(timeout=5))["body"]
    assert body.startswith(b"event: update\ndata: ")
    assert body.endswith(b"\n\n")
    await sync_to_async(Token.objects.filter(pk=api_token.pk).update)(
        revoked=timezone.now()
    )
    await sync_to_async(publish)(
        f"user:{identity.pk}", {"kind": "delete", "id": str(post.pk)}
    )
    assert (await client.receive_output(timeout=5))["more_body"] is False
    await client.wait()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_transport_errors_and_health() -> None:
    client = connection(path=PREFIX + "/health", websocket=False)
    await client.send_input({"type": "http.request"})
    assert (await client.receive_output())["status"] == 200
    assert (await client.receive_output())["body"] == b"OK"
    await client.wait()
    client = connection(websocket=False, query="access_token=invalid&stream=user")
    await client.send_input({"type": "http.request"})
    assert (await client.receive_output())["status"] == 401
    await client.wait()
    client = connection(query="access_token=invalid")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output())["type"] == "websocket.close"
    await client.wait()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mute,notifications,delivered",
    [(False, False, False), (True, True, False), (True, False, True)],
)
def test_notification_respects_blocks(
    api_token,
    identity,
    other_identity,
    mute: bool,
    notifications: bool,
    delivered: bool,
) -> None:
    event = TimelineEvent.objects.create(
        identity=identity, type="followed", subject_identity=other_identity
    )
    Block.objects.create(
        source=identity,
        target=other_identity,
        mute=mute,
        include_notifications=notifications,
    )
    assert (
        bool(
            render(api_token, "user:notification", {"kind": "timeline", "id": event.pk})
        )
        is delivered
    )


@pytest.mark.django_db
def test_unboost_publishes_wrapper_id(
    identity, other_identity, django_capture_on_commit_callbacks
) -> None:
    post = make_post(other_identity)
    boost = PostInteraction.objects.create(
        identity=other_identity, post=post, type="boost"
    )
    TimelineEvent.objects.create(
        identity=identity,
        type="boost",
        subject_post=post,
        subject_identity=other_identity,
        subject_post_interaction=boost,
    )
    with (
        patch("api.streaming_events.publisher") as mock,
        django_capture_on_commit_callbacks(execute=True),
    ):
        TimelineEvent.delete_post_interaction(identity, boost)
    message = json.loads(mock.return_value.publish.call_args.args[1])
    assert message == {"kind": "delete", "id": str(boost.pk)}


@pytest.mark.django_db
def test_private_list_delete_is_authorized(
    api_token, identity, other_identity, django_capture_on_commit_callbacks
) -> None:
    alist = List.objects.create(
        identity=identity, title="List", replies_policy="list", exclusive=False
    )
    alist.members.add(other_identity)
    post = make_post(other_identity, visibility=Post.Visibilities.mentioned)
    with patch("api.streaming_events.publish_many") as mock:
        publish_post(post, "delete")
    message = mock.call_args.args[0][0][1]
    assert not render(api_token, "list", message, str(alist.pk))
    message["mentions"] = [identity.pk]
    assert render(api_token, "list", message, str(alist.pk))[0]["event"] == "delete"
    list_id = str(alist.pk)
    alist.delete()
    assert not render(api_token, "list", message, list_id)


@pytest.mark.django_db
def test_publish_failure_does_not_break_writes(
    identity, django_capture_on_commit_callbacks
) -> None:
    with (
        patch("api.streaming_events.publisher") as mock,
        django_capture_on_commit_callbacks(execute=True),
    ):
        mock.return_value.publish.side_effect = ConnectionError("unavailable")
        event = TimelineEvent.objects.create(
            identity=identity, type="followed", subject_identity=identity
        )
    assert TimelineEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_instance_streaming_discovery(api_client) -> None:
    assert (
        api_client.get("/api/v1/instance", secure=True, HTTP_HOST="example.com").json()[
            "urls"
        ]["streaming_api"]
        == "wss://example.com"
    )
    assert (
        api_client.get("/api/v2/instance", secure=True, HTTP_HOST="example.com").json()[
            "configuration"
        ]["urls"]["streaming"]
        == "wss://example.com"
    )
    assert (
        api_client.get("/api/v1/instance", HTTP_HOST="example.com:8000").json()["urls"][
            "streaming_api"
        ]
        == "ws://example.com:8000"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_heartbeat_and_disconnect(api_token, monkeypatch) -> None:
    monkeypatch.setattr("api.streaming.HEARTBEAT", 0.02)
    client = connection(
        path=PREFIX + "/user/notification",
        query=f"access_token={api_token.token}",
        websocket=False,
    )
    await client.send_input({"type": "http.request", "body": b""})
    assert (await client.receive_output(timeout=5))["status"] == 200
    await client.receive_output()
    assert (await client.receive_output(timeout=5))["body"] == b":thump\n\n"
    await disconnect(client, websocket=False)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_http_discovery_and_unknown_stream(api_token) -> None:
    for path, query, expected in [
        (PREFIX, "", 404),
        (PREFIX + "/unknown", f"access_token={api_token.token}", 400),
        (
            PREFIX + "/list",
            f"access_token={api_token.token}&list=9999999999999999999",
            404,
        ),
    ]:
        client = connection(path=path, query=query, websocket=False)
        await client.send_input({"type": "http.request", "body": b""})
        assert (await client.receive_output(timeout=5))["status"] == expected
        await client.wait()


@pytest.mark.django_db
@pytest.mark.parametrize("mirror_write", [False, True])
def test_author_stream_is_published_from_post_processing(
    api_token, identity, mirror_write: bool
) -> None:
    post = make_post(identity)
    with patch("api.streaming_events.publish_many") as messages:
        if mirror_write:
            TimelineEvent.objects.bulk_create(
                [
                    TimelineEvent(
                        identity=identity,
                        type="post",
                        subject_post=post,
                        subject_identity=identity,
                    )
                ]
            )
        else:
            TimelineEvent.add_post(identity, post)
        messages.assert_not_called()
        with (
            patch.object(PostStates, "targets_fan_out"),
            patch("activities.models.post._attach_preview_card"),
        ):
            PostStates.handle_new(post)
        payloads = dict(messages.call_args.args[0])
        own = payloads[f"user:{identity.pk}"]
        events = render(api_token, "user", own)
        assert len(events) == 1
        assert json.loads(events[0]["payload"])["id"] == str(post.pk)
        assert not render(api_token, "user", payloads["posts"])
        messages.reset_mock()
        TimelineEvent.add_post(identity, post)
        messages.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("mirror_write", [False, True])
def test_conversation_stream_is_published_from_post_processing(
    api_token, identity, other_identity, remote_identity, mirror_write: bool
) -> None:
    post = make_post(identity, visibility=Post.Visibilities.mentioned)
    post.mentions.add(other_identity, remote_identity)
    participants = {identity.pk, other_identity.pk, remote_identity.pk}
    with patch("api.streaming_events.publish_many") as messages:
        if mirror_write:
            conversation = Conversation.get_or_create_for_participants(participants)
            ConversationMembership.objects.bulk_create(
                [
                    ConversationMembership(conversation=conversation, identity_id=pk)
                    for pk in participants
                ]
            )
            Conversation.objects.filter(pk=conversation.pk).update(last_post=post)
            Post.objects.filter(pk=post.pk).update(conversation=conversation)
        else:
            Conversation.update_for_post(post)
        messages.assert_not_called()
        post.refresh_from_db()
        with (
            patch.object(PostStates, "targets_fan_out"),
            patch("activities.models.post._attach_preview_card"),
        ):
            for handler in (PostStates.handle_new, PostStates.handle_edited):
                handler(post)
                conversations = [
                    (name, payload)
                    for name, payload in messages.call_args.args[0]
                    if payload["kind"] == "conversation"
                ]
                assert {name for name, _ in conversations} == {
                    f"user:{identity.pk}",
                    f"user:{other_identity.pk}",
                }
                assert len(conversations) == 2
                events = render(api_token, "direct", conversations[0][1])
                assert len(events) == 1
                assert json.loads(events[0]["payload"])["last_status"]["id"] == str(
                    post.pk
                )


@pytest.mark.django_db
def test_old_remote_posts_do_not_publish(remote_identity, settings) -> None:
    settings.FANOUT_LIMIT_DAYS = 1
    post = make_post(remote_identity)
    post.published = timezone.now() - timedelta(days=2)
    post.save()
    with (
        patch("activities.models.post.publish_post") as publish_mock,
        patch.object(PostStates, "targets_fan_out") as fanout,
        patch("activities.models.post._attach_preview_card"),
    ):
        PostStates.handle_new(post)
    publish_mock.assert_not_called()
    fanout.assert_not_called()


@pytest.mark.django_db
def test_bulk_timeline_cleanup_stays_a_single_delete(
    identity, django_assert_num_queries
) -> None:
    post = make_post(identity)
    TimelineEvent.objects.bulk_create(
        [
            TimelineEvent(
                identity=identity,
                type="post",
                subject_post=post,
                subject_identity=identity,
            )
            for _ in range(500)
        ]
    )
    with (
        patch("api.streaming_events.publish_many") as messages,
        django_assert_num_queries(1),
    ):
        TimelineEvent.objects.filter(subject_post=post).delete()
    messages.assert_not_called()


@pytest.mark.django_db
def test_pruning_does_not_publish_deletion(remote_identity) -> None:
    post = make_post(remote_identity)
    with patch("api.streaming_events.publish_many") as messages:
        Post.objects.filter(pk=post.pk).delete()
    messages.assert_not_called()


@pytest.mark.django_db
def test_confirmed_remote_deletion_publishes_to_existing_readers(
    identity, remote_identity
) -> None:
    post = make_post(remote_identity)
    TimelineEvent.add_post(identity, post)
    post_id = str(post.pk)
    with patch("api.streaming_events.publish_many") as messages:
        post.perform_remote_deletion()
    payloads = dict(messages.call_args.args[0])
    assert payloads["posts"]["event"] == "delete"
    assert payloads[f"user:{identity.pk}"] == {"kind": "delete", "ids": [post_id]}
    assert not Post.objects.filter(pk=payloads["posts"]["id"]).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("stream", ["user", "user:notification", "direct"])
def test_unrelated_posts_need_no_database_queries(
    api_token, django_assert_num_queries, stream: str
) -> None:
    with django_assert_num_queries(0):
        assert (
            render(api_token, stream, {"kind": "post", "event": "update", "id": 1})
            == []
        )
    assert channel("posts") not in StreamingApplication.channels(
        {Subscription(stream)}, api_token.identity_id
    )


@pytest.mark.django_db
def test_overlapping_subscriptions_share_serialization(
    api_token, identity, other_identity
) -> None:
    post = make_post(other_identity, hashtags=["neodb"])
    alist = List.objects.create(
        identity=identity, title="Overlap", replies_policy="list", exclusive=False
    )
    alist.members.add(other_identity)
    subscriptions = {
        Subscription("public"),
        Subscription("hashtag", "neodb"),
        Subscription("list", str(alist.pk)),
    }
    with patch.object(
        schemas.Status, "map_from_post", wraps=schemas.Status.map_from_post
    ) as serialize:
        events = render_events(
            api_token.token,
            subscriptions,
            {"kind": "post", "event": "update", "id": post.pk},
        )
    assert len(events) == 3
    assert len({event["payload"] for event in events}) == 1
    serialize.assert_called_once()
    alist.members.clear()
    events = render_events(
        api_token.token,
        subscriptions,
        {"kind": "post", "event": "update", "id": post.pk},
    )
    assert len(events) == 2
    assert all(event["stream"][0] != "list" for event in events)


@pytest.mark.django_db
def test_configuration_refresh_is_cached(api_token, monkeypatch) -> None:
    monkeypatch.setattr(Config, "__forced__", False, raising=False)
    monkeypatch.setattr("api.streaming._config_loaded_at", -10)
    with patch.object(Config, "load_system", return_value=Config.system) as load:
        with patch("api.streaming.time.monotonic", return_value=10):
            refresh_config()
            refresh_config()
        assert load.call_count == 1
        with patch("api.streaming.time.monotonic", return_value=16):
            refresh_config()
        assert load.call_count == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "bad_message", ["not JSON", '{"kind":"post","event":"update","id":"invalid"}']
)
async def test_bad_event_does_not_disconnect_other_events(
    api_token, identity, bad_message: str
) -> None:
    client = connection(query=f"stream=public&access_token={api_token.token}")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    await sync_to_async(publisher(redis_url()).publish)(channel("posts"), bad_message)
    post = await sync_to_async(make_post)(identity)
    await sync_to_async(publish_post)(post, "update")
    event = json.loads((await client.receive_output(timeout=5))["text"])
    assert json.loads(event["payload"])["id"] == str(post.pk)
    await disconnect(client)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_query_token_takes_precedence_over_subprotocol(api_token) -> None:
    client = connection(
        query=f"stream=user&access_token={api_token.token}",
        subprotocols=["unrelated-protocol"],
    )
    await client.send_input({"type": "websocket.connect"})
    response = await client.receive_output(timeout=5)
    assert response["type"] == "websocket.accept"
    assert response["subprotocol"] is None
    await disconnect(client)


@pytest.mark.django_db
def test_post_fanout_uses_one_pipeline_after_commit(
    identity, other_identity, django_capture_on_commit_callbacks
) -> None:
    post = make_post(other_identity)
    TimelineEvent.add_post(identity, post)
    with patch("api.streaming_events.publisher") as mock:
        pipeline = mock.return_value.pipeline.return_value.__enter__.return_value
        with django_capture_on_commit_callbacks(execute=True):
            publish_post(post, "status.update")
            mock.assert_not_called()
        mock.return_value.pipeline.assert_called_once_with(transaction=False)
        pipeline.execute.assert_called_once()
        assert {call.args[0] for call in pipeline.publish.call_args_list} == {
            channel("posts"),
            channel(f"user:{identity.pk}"),
            channel(f"user:{other_identity.pk}"),
        }
        mock.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(ValueError), transaction.atomic():
                publish_post(post, "delete")
                raise ValueError("rollback")
        mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_targeted_edit_and_delete_delivery(
    api_token, identity, other_identity
) -> None:
    post = await sync_to_async(make_post)(other_identity)
    await sync_to_async(TimelineEvent.add_post)(identity, post)
    boost = await sync_to_async(PostInteraction.objects.create)(
        identity=other_identity, post=post, type="boost"
    )
    await sync_to_async(TimelineEvent.objects.create)(
        identity=identity,
        type="boost",
        subject_post=post,
        subject_identity=other_identity,
        subject_post_interaction=boost,
    )
    client = connection(query=f"stream=user&access_token={api_token.token}")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(channel("posts"))
    assert counts[0][1] == 0
    await sync_to_async(publish_post)(post, "status.update")
    event = json.loads((await client.receive_output(timeout=5))["text"])
    assert event["event"] == "status.update"
    assert json.loads(event["payload"])["id"] == str(post.pk)
    await sync_to_async(publish_post)(post, "delete")
    deleted = [
        json.loads((await client.receive_output(timeout=5))["text"]) for _ in range(2)
    ]
    assert {event["payload"] for event in deleted} == {str(post.pk), str(boost.pk)}
    assert all(event["event"] == "delete" for event in deleted)
    assert await client.receive_nothing()
    await disconnect(client)


def test_public_connections_only_subscribe_to_posts() -> None:
    assert StreamingApplication.channels({Subscription("public")}, 1) == {
        channel("posts")
    }
    assert StreamingApplication.channels(
        {Subscription("public"), Subscription("user")}, 1
    ) == {channel("posts"), channel("user:1")}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_messages_during_subscribe_are_delivered(
    api_token, identity, monkeypatch
) -> None:
    post = await sync_to_async(make_post)(identity)
    original = PubSub.get_message
    injected = False

    async def get_message(
        self: PubSub, ignore_subscribe_messages: bool = False, timeout: float = 0
    ) -> dict | None:
        nonlocal injected
        if not injected:
            injected = True
            return {
                "type": "message",
                "data": json.dumps({"kind": "post", "event": "update", "id": post.pk}),
            }
        return await original(
            self, ignore_subscribe_messages=ignore_subscribe_messages, timeout=timeout
        )

    monkeypatch.setattr(PubSub, "get_message", get_message)
    client = connection(query=f"stream=public&access_token={api_token.token}")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    event = json.loads((await client.receive_output(timeout=5))["text"])
    assert json.loads(event["payload"])["id"] == str(post.pk)
    await disconnect(client)


@pytest.mark.asyncio
async def test_dedicated_backend_rejects_rest() -> None:
    for path, expected in [("/api/v1/instance", 404), (PREFIX + "/health", 200)]:
        client = ApplicationCommunicator(
            streaming_application,
            {
                "type": "http",
                "path": path,
                "method": "GET",
                "query_string": b"",
                "headers": [],
            },
        )
        await client.send_input({"type": "http.request", "body": b""})
        assert (await client.receive_output())["status"] == expected
        await client.receive_output()
        await client.wait()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_database_work_has_bounded_concurrency() -> None:
    limit = settings.SETUP.STREAMING_DB_THREADS
    release = threading.Event()
    full = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    def work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == limit:
                full.set()
        try:
            assert release.wait(timeout=5)
        finally:
            with lock:
                active -= 1

    tasks = [
        asyncio.create_task(database_sync_to_async(work)()) for _ in range(limit * 2)
    ]
    try:
        async with asyncio.timeout(5):
            while not full.is_set():
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert peak == limit
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert peak == limit
    assert active == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_database_outage_rejects_connection(api_token) -> None:
    client = connection(
        query=f"stream=user&access_token={api_token.token}", websocket=False
    )
    with patch(
        "api.streaming.authenticate", side_effect=OperationalError("unavailable")
    ):
        await client.send_input({"type": "http.request", "body": b""})
        assert (await client.receive_output(timeout=5))["status"] == 503
        await client.wait()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_redis_outage_rejects_connection(api_token) -> None:
    client = connection(
        query=f"stream=user&access_token={api_token.token}", websocket=False
    )
    with patch.object(PubSub, "subscribe", side_effect=ConnectionError("unavailable")):
        await client.send_input({"type": "http.request", "body": b""})
        assert (await client.receive_output(timeout=5))["status"] == 503
        await client.wait()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_database_outage_on_heartbeat_closes_cleanly(
    api_token, monkeypatch
) -> None:
    monkeypatch.setattr("api.streaming.HEARTBEAT", 0.1)
    client = connection(query=f"stream=user&access_token={api_token.token}")
    await client.send_input({"type": "websocket.connect"})
    assert (await client.receive_output(timeout=5))["type"] == "websocket.accept"
    with patch(
        "api.streaming.authenticate", side_effect=OperationalError("unavailable")
    ):
        assert (await client.receive_output(timeout=5)) == {
            "type": "websocket.close",
            "code": 1011,
        }
        await client.wait()
    counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(
        channel(f"user:{api_token.identity_id}")
    )
    assert counts[0][1] == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_disconnected_send_releases_subscription(
    api_token, identity, other_identity
) -> None:
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()

    async def send(message: dict) -> None:
        if message["type"] == "websocket.send":
            raise OSError("Client disconnected")
        await outgoing.put(message)

    scope = {
        "type": "websocket",
        "path": PREFIX,
        "query_string": f"stream=user&access_token={api_token.token}".encode(),
        "headers": [],
    }
    task = asyncio.create_task(StreamingApplication()(scope, incoming.get, send))
    try:
        await incoming.put({"type": "websocket.connect"})
        assert (await asyncio.wait_for(outgoing.get(), 5))["type"] == "websocket.accept"
        post = await sync_to_async(make_post)(other_identity)
        await sync_to_async(TimelineEvent.add_post)(identity, post)
        await asyncio.wait_for(task, 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    counts = await sync_to_async(publisher(redis_url()).pubsub_numsub)(
        channel(f"user:{identity.pk}")
    )
    assert counts[0][1] == 0


@pytest.mark.django_db
def test_disabled_streaming_skips_publishing_and_queries(
    identity, settings, monkeypatch, django_assert_num_queries
) -> None:
    post = make_post(identity)
    monkeypatch.setattr(settings.SETUP, "STREAMING_ENABLED", False)
    with patch("api.streaming_events.publisher") as redis, django_assert_num_queries(0):
        publish_post(post, "update")
        publish_post(post, "status.update")
        publish_post(post, "delete")
        publish("posts", {"kind": "post", "id": post.pk, "event": "update"})
    redis.assert_not_called()


@pytest.mark.django_db
def test_disabled_streaming_rest_and_discovery(
    api_client, settings, monkeypatch
) -> None:
    monkeypatch.setattr(settings.SETUP, "STREAMING_ENABLED", False)
    assert api_client.get("/api/v1/instance").json()["urls"] == {}
    assert api_client.get("/api/v2/instance").json()["configuration"]["urls"] == {}
    assert api_client.get("/api/v1/accounts/verify_credentials").status_code == 200
    assert api_client.get("/api/v1/timelines/home").status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("websocket", [True, False])
async def test_deployment_disabled_streaming_has_no_dependencies(
    settings, monkeypatch, websocket: bool
) -> None:
    monkeypatch.setattr(settings.SETUP, "STREAMING_ENABLED", False)
    client = connection(query="stream=user&access_token=invalid", websocket=websocket)
    with patch("api.streaming.Redis.from_url") as redis:
        await client.send_input(
            {"type": "websocket.connect"}
            if websocket
            else {"type": "http.request", "body": b""}
        )
        response = await client.receive_output(timeout=5)
        if websocket:
            assert response["type"] == "websocket.close"
        else:
            assert response["status"] == 404
        await client.wait()
    redis.assert_not_called()


def test_streaming_setting_defaults_off() -> None:
    assert type(settings.SETUP).model_fields["STREAMING_ENABLED"].default is False
