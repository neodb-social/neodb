from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.utils import timezone

from activities.models import (
    Hashtag,
    Post,
    PostInteraction,
    TimelineEvent,
)
from activities.services import PostService, TimelineService
from core.ld import format_ld_date
from stator.exceptions import TryAgainLater
from users.models import Block, Follow, Identity, InboxMessage
from users.services import IdentityService


@pytest.mark.django_db
@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("blocked", ["full", "mute", "no"])
def test_mentioned(
    identity: Identity,
    other_identity: Identity,
    remote_identity: Identity,
    stator,
    local: bool,
    blocked: bool,
):
    """
    Ensures that a new or incoming post that mentions a local identity results in a
    mentioned timeline event, unless the author is blocked.
    """
    if local:
        Post.create_local(author=other_identity, content=f"Hello @{identity.handle}!")
    else:
        # Create an inbound new post message
        message = {
            "id": "test",
            "type": "Create",
            "actor": remote_identity.actor_uri,
            "object": {
                "id": "https://remote.test/test-post",
                "type": "Note",
                "published": format_ld_date(timezone.now()),
                "attributedTo": remote_identity.actor_uri,
                "content": f"Hello @{identity.handle}!",
                "tag": {
                    "type": "Mention",
                    "href": identity.actor_uri,
                    "name": f"@{identity.handle}",
                },
            },
        }
        InboxMessage.objects.create(message=message)

    # Implement any blocks
    author = other_identity if local else remote_identity
    if blocked == "full":
        Block.create_local_block(identity, author)
    elif blocked == "mute":
        Block.create_local_mute(identity, author)

    # Run stator thrice - to receive the post, make fanouts and then process them
    stator.run_single_cycle()
    stator.run_single_cycle()
    stator.run_single_cycle()

    if blocked in ["full", "mute"]:
        # Verify we were not mentioned
        assert not TimelineEvent.objects.filter(
            type=TimelineEvent.Types.mentioned, identity=identity
        ).exists()
    else:
        # Verify we got mentioned
        event = TimelineEvent.objects.filter(
            type=TimelineEvent.Types.mentioned, identity=identity
        ).first()
        assert event
        assert event.subject_identity == author
        assert "Hello " in event.subject_post.content


@pytest.mark.django_db
@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("type", ["like", "boost"])
@pytest.mark.parametrize("blocked", ["full", "mute", "mute_with_notifications", "no"])
def test_interaction_local_post(
    identity: Identity,
    other_identity: Identity,
    remote_identity: Identity,
    stator,
    local: bool,
    type: str,
    blocked: bool,
):
    """
    Ensures that a like of a local Post notifies its author
    """
    post = Post.create_local(author=identity, content="I love birds!")
    if local:
        if type == "boost":
            PostService(post).boost_as(other_identity)
        else:
            PostService(post).like_as(other_identity)
    else:
        if type == "boost":
            message = {
                "id": "test",
                "type": "Announce",
                "to": "as:Public",
                "actor": remote_identity.actor_uri,
                "object": post.object_uri,
            }
        else:
            message = {
                "id": "test",
                "type": "Like",
                "actor": remote_identity.actor_uri,
                "object": post.object_uri,
            }
        InboxMessage.objects.create(message=message)

    # Implement any blocks
    interactor = other_identity if local else remote_identity
    if blocked == "full":
        Block.create_local_block(identity, interactor)
    elif blocked == "mute":
        Block.create_local_mute(identity, interactor)
    elif blocked == "mute_with_notifications":
        Block.create_local_mute(identity, interactor, include_notifications=True)

    # Run stator thrice - to receive the post, make fanouts and then process them
    stator.run_single_cycle()
    stator.run_single_cycle()
    stator.run_single_cycle()

    timeline_event_type = (
        TimelineEvent.Types.boosted if type == "boost" else TimelineEvent.Types.liked
    )
    if blocked in ["full", "mute_with_notifications"]:
        # Verify we did not get an event
        assert not TimelineEvent.objects.filter(
            type=timeline_event_type, identity=identity
        ).exists()
    else:
        # Verify we got an event
        event = TimelineEvent.objects.filter(
            type=timeline_event_type, identity=identity
        ).first()
        assert event
        assert event.subject_identity == interactor


@pytest.mark.django_db
@pytest.mark.parametrize("old", [True, False])
def test_old_new_post(
    identity: Identity,
    remote_identity: Identity,
    stator,
    old: bool,
):
    """
    Ensures that old remote posts don't appear on the timeline, but new ones do.
    """
    # Follow the remote user
    Follow.create_local(identity, remote_identity)
    # Create an inbound new post message
    message = {
        "id": "test",
        "type": "Create",
        "actor": remote_identity.actor_uri,
        "object": {
            "id": "https://remote.test/test-post",
            "type": "Note",
            "published": "2022-01-01T00:00:00Z"
            if old
            else format_ld_date(timezone.now()),
            "attributedTo": remote_identity.actor_uri,
            "content": f"Hello @{identity.handle}!",
            "tag": {
                "type": "Mention",
                "href": identity.actor_uri,
                "name": f"@{identity.handle}",
            },
        },
    }
    InboxMessage.objects.create(message=message)

    # Run stator thrice - to receive the post, make fanouts and then process them
    stator.run_single_cycle()
    stator.run_single_cycle()
    stator.run_single_cycle()

    if old:
        # Verify it did not appear on the timeline
        assert not TimelineEvent.objects.filter(
            type=TimelineEvent.Types.post, identity=identity
        ).exists()
    else:
        # Verify it appeared on the timeline
        event = TimelineEvent.objects.filter(
            type=TimelineEvent.Types.post, identity=identity
        ).first()
        assert event
        assert "Hello " in event.subject_post.content


@pytest.mark.django_db
@pytest.mark.parametrize("full", [True, False])
def test_clear_timeline(
    identity: Identity,
    remote_identity: Identity,
    stator,
    full: bool,
):
    """
    Ensures that timeline clearing works as expected.
    """
    # Follow the remote user
    service = IdentityService(identity)
    service.follow(remote_identity)
    # Create an inbound new post message mentioning us
    message = {
        "id": "test",
        "type": "Create",
        "actor": remote_identity.actor_uri,
        "object": {
            "id": "https://remote.test/test-post",
            "type": "Note",
            "published": format_ld_date(timezone.now()),
            "attributedTo": remote_identity.actor_uri,
            "content": f"Hello @{identity.handle}!",
            "tag": {
                "type": "Mention",
                "href": identity.actor_uri,
                "name": f"@{identity.handle}",
            },
        },
    }
    InboxMessage.objects.create(message=message)

    # Run stator thrice - to receive the post, make fanouts and then process them
    stator.run_single_cycle()
    stator.run_single_cycle()
    stator.run_single_cycle()

    # Make sure it appeared on our timeline as a post and a mentioned
    assert TimelineEvent.objects.filter(
        type=TimelineEvent.Types.post, identity=identity
    ).exists()
    assert TimelineEvent.objects.filter(
        type=TimelineEvent.Types.mentioned, identity=identity
    ).exists()

    # Now, submit either a user block (for full clear) or unfollow (for post clear)
    if full:
        service.block(remote_identity)
    else:
        service.unfollow(remote_identity)

    # Run stator twice to process the timeline clear message
    stator.run_single_cycle()
    stator.run_single_cycle()

    # Verify that the right things vanished
    assert not TimelineEvent.objects.filter(
        type=TimelineEvent.Types.post, identity=identity
    ).exists()
    assert TimelineEvent.objects.filter(
        type=TimelineEvent.Types.mentioned, identity=identity
    ).exists() == (not full)


@pytest.mark.django_db
@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("blocked", ["full", "mute", "no"])
def test_hashtag_followed(
    identity: Identity,
    other_identity: Identity,
    remote_identity: Identity,
    stator,
    local: bool,
    blocked: bool,
):
    """
    Ensure that a new or incoming post with a hashtag followed by a local entity
    results in a timeline event, unless the author is blocked.
    """
    hashtag = Hashtag.ensure_hashtag("takahe")
    identity.hashtag_follows.get_or_create(hashtag=hashtag)

    if local:
        Post.create_local(author=other_identity, content="Hello from #Takahe!")
    else:
        # Create an inbound new post message
        message = {
            "id": "test",
            "type": "Create",
            "actor": remote_identity.actor_uri,
            "object": {
                "id": "https://remote.test/test-post",
                "type": "Note",
                "published": format_ld_date(timezone.now()),
                "attributedTo": remote_identity.actor_uri,
                "to": "as:Public",
                "content": '<p>Hello from <a href="https://remote.test/tags/takahe/" rel="tag">#Takahe</a>!',
                "tag": {
                    "type": "Hashtag",
                    "href": "https://remote.test/tags/takahe/",
                    "name": "#Takahe",
                },
            },
        }
        InboxMessage.objects.create(message=message)

    # Implement any blocks
    author = other_identity if local else remote_identity
    if blocked == "full":
        Block.create_local_block(identity, author)
    elif blocked == "mute":
        Block.create_local_mute(identity, author)

    # Run stator thrice - to receive the post, make fanouts and then process them
    stator.run_single_cycle()
    stator.run_single_cycle()
    stator.run_single_cycle()

    if blocked in ["full", "mute"]:
        # Verify post is not in timeline
        assert not TimelineEvent.objects.filter(
            type=TimelineEvent.Types.post, identity=identity
        ).exists()
    else:
        # Verify post is in timeline
        event = TimelineEvent.objects.filter(
            type=TimelineEvent.Types.post, identity=identity
        ).first()
        assert event
        assert "Hello from " in event.subject_post.content


@pytest.mark.django_db
def test_exclusive_list_excludes_from_home_timeline(
    identity: Identity,
    other_identity: Identity,
    config_system,
):
    """Posts and boosts from members of exclusive lists are excluded from home timeline."""
    # identity follows other_identity
    alist = identity.lists.create(
        title="Exclusive", replies_policy="list", exclusive=True
    )
    alist.members.add(other_identity)

    # Add a post and boost by other_identity directly to identity's timeline
    post = Post.create_local(author=other_identity, content="<p>Hello</p>")
    TimelineEvent.add_post(identity=identity, post=post)

    boost_post = Post.create_local(author=other_identity, content="<p>Boosted</p>")
    boost = PostInteraction.objects.create(
        identity=other_identity,
        post=boost_post,
        type=PostInteraction.Types.boost,
    )
    TimelineEvent.add_post_interaction(identity=identity, interaction=boost)

    home = list(TimelineService(identity).home())
    assert not any(
        e.type == TimelineEvent.Types.post and e.subject_post_id == post.pk
        for e in home
    ), "post from exclusive list member should be excluded from home timeline"
    assert not any(
        e.type == TimelineEvent.Types.boost
        and e.subject_identity_id == other_identity.pk
        for e in home
    ), "boost from exclusive list member should be excluded from home timeline"


@pytest.mark.django_db
def test_non_exclusive_list_does_not_exclude_from_home_timeline(
    identity: Identity,
    other_identity: Identity,
    config_system,
):
    """Posts from members of non-exclusive lists still appear in home timeline."""
    alist = identity.lists.create(
        title="Normal", replies_policy="list", exclusive=False
    )
    alist.members.add(other_identity)

    post = Post.create_local(author=other_identity, content="<p>Hello</p>")
    TimelineEvent.add_post(identity=identity, post=post)

    home = list(TimelineService(identity).home())
    assert any(
        e.type == TimelineEvent.Types.post and e.subject_post_id == post.pk
        for e in home
    ), "post from non-exclusive list member should remain in home timeline"


@pytest.mark.django_db
def test_handle_clear_timeline_translates_deadlock_to_tryagainlater(
    identity: Identity,
    other_identity: Identity,
):
    """
    A Postgres deadlock during ClearTimeline cleanup should surface as
    TryAgainLater so Stator silently reschedules instead of logging the
    OperationalError. Other OperationalErrors must still propagate.
    """
    message = {"actor": str(identity.pk), "object": str(other_identity.pk)}

    deadlock = OperationalError(
        "deadlock detected\nDETAIL: Process A waits for ShareLock..."
    )

    class _BoomQuerySet:
        def filter(self, *args, **kwargs):
            return self

        def delete(self):
            raise deadlock

    with patch.object(TimelineEvent, "objects", _BoomQuerySet()):
        with pytest.raises(TryAgainLater):
            TimelineEvent.handle_clear_timeline(message)

    other = OperationalError("connection terminated")

    class _OtherErrorQuerySet:
        def filter(self, *args, **kwargs):
            return self

        def delete(self):
            raise other

    with patch.object(TimelineEvent, "objects", _OtherErrorQuerySet()):
        with pytest.raises(OperationalError):
            TimelineEvent.handle_clear_timeline(message)
