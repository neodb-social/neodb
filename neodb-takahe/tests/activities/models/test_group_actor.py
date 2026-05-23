import pytest
from django.utils import timezone

from activities.models import (
    FanOut,
    Post,
    PostInteraction,
    PostInteractionStates,
    TimelineEvent,
)
from activities.models.fan_out import FanOutStates
from users.models import Follow, FollowStates


@pytest.fixture
def group_identity(identity_factory):
    """Create a local group identity"""
    identity = identity_factory(username="group_test", actor_type="group")
    return identity


@pytest.mark.django_db
def test_group_identity_property(identity, group_identity):
    """Test the is_group property"""
    assert not identity.is_group
    assert group_identity.is_group


@pytest.mark.django_db
def test_group_actor_auto_boost_post_from_followed_user(
    identity, remote_identity, group_identity, config_system
):
    """Test that group actor automatically boosts posts from users it follows"""
    # Create a follow relationship from group to another user
    follow = Follow.objects.create(
        source=group_identity,
        target=identity,
    )
    follow.transition_perform(FollowStates.accepted)

    # Create a post by the followed user
    post = Post.create_local(
        author=identity,
        content="<p>Test post from followed user</p>",
    )

    # Create a fanout to the group
    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.post,
        subject_post=post,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Check that the group boosted the post
    boost = PostInteraction.objects.filter(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state__in=PostInteractionStates.group_active(),
    )

    assert boost.exists()


@pytest.mark.django_db
def test_group_actor_auto_boost_post_with_mention(
    identity, remote_identity, group_identity, config_system
):
    """Test that group actor automatically boosts posts that mention it"""
    # Create a post that mentions the group
    post = Post.create_local(
        author=identity,
        content=f'<p>Test post mentioning <span class="h-card"><a href="https://example.com/@{group_identity.username}@example.com">@{group_identity.username}</a></span></p>',
    )

    # Add the mention
    post.mentions.add(group_identity)

    # Create a fanout to the group
    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.post,
        subject_post=post,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Check that the group boosted the post
    boost = PostInteraction.objects.filter(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state__in=PostInteractionStates.group_active(),
    )

    assert boost.exists()


@pytest.mark.django_db
def test_group_actor_no_auto_boost_own_post(group_identity, config_system):
    """Test that group actor doesn't boost its own posts"""
    # Create a post by the group itself
    post = Post.create_local(
        author=group_identity,
        content="<p>Test post from the group</p>",
    )

    # Create a fanout to the group
    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.post,
        subject_post=post,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Check that the group did not boost its own post
    boost = PostInteraction.objects.filter(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
    )

    assert not boost.exists()


@pytest.mark.django_db
def test_group_actor_unboost_on_post_deletion(identity, group_identity, config_system):
    """Test that group actor removes boost when a post is deleted"""
    # Create a post and a boost by the group
    post = Post.create_local(
        author=identity,
        content="<p>Test post to be deleted</p>",
    )

    # Create boost manually
    boost = PostInteraction.objects.create(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        published=timezone.now(),
    )
    boost.transition_perform(PostInteractionStates.new)
    boost.transition_perform(PostInteractionStates.fanned_out)

    # Verify the boost exists and is active
    assert PostInteraction.objects.filter(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state__in=PostInteractionStates.group_active(),
    ).exists()

    # Create a post deletion fanout
    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.post_deleted,
        subject_post=post,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Verify the boost is now undone
    boost.refresh_from_db()
    assert boost.state == PostInteractionStates.undone


@pytest.mark.django_db
def test_group_actor_boost_from_timeline_boost(
    identity, remote_identity, group_identity, config_system
):
    """Test that group actor automatically boosts when it receives a boost in its timeline"""
    # Create a post by remote identity
    post = Post.create_local(
        author=remote_identity,
        content="<p>Test post to be boosted</p>",
    )

    # Create a boost by identity
    boost = PostInteraction.objects.create(
        identity=identity,
        post=post,
        type=PostInteraction.Types.boost,
        published=timezone.now(),
    )
    boost.transition_perform(PostInteractionStates.new)
    boost.transition_perform(PostInteractionStates.fanned_out)

    # Create a fanout for the boost to the group
    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.interaction,
        subject_post=post,
        subject_post_interaction=boost,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Check that the group also boosted the post
    group_boost = PostInteraction.objects.filter(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state__in=PostInteractionStates.group_active(),
    )

    assert group_boost.exists()


@pytest.mark.django_db
def test_group_actor_unboost_with_timeline_check(
    identity, group_identity, config_system
):
    """Test that group actor checks timeline before unboosting a post"""
    # Create a post
    post = Post.create_local(
        author=identity,
        content="<p>Test post with timeline check</p>",
    )

    # Create boost manually
    boost = PostInteraction.objects.create(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        published=timezone.now(),
    )
    boost.transition_perform(PostInteractionStates.new)
    boost.transition_perform(PostInteractionStates.fanned_out)

    # Create a timeline event for this post (as if group was mentioned or follows author)
    TimelineEvent.objects.create(
        identity=group_identity,
        type=TimelineEvent.Types.post,
        subject_post=post,
    )

    # Create an unboost fanout
    interaction_undo = PostInteraction.objects.create(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state=PostInteractionStates.undone,
    )

    fanout = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.undo_interaction,
        subject_post=post,
        subject_post_interaction=interaction_undo,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout)

    # Verify the boost is still active since post is in timeline
    boost.refresh_from_db()
    assert boost.state == PostInteractionStates.fanned_out

    # Now remove the timeline event and try again
    TimelineEvent.objects.filter(
        identity=group_identity,
        subject_post=post,
    ).delete()

    # Create a new unboost fanout
    interaction_undo2 = PostInteraction.objects.create(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        state=PostInteractionStates.undone,
    )

    fanout2 = FanOut.objects.create(
        identity=group_identity,
        type=FanOut.Types.undo_interaction,
        subject_post=post,
        subject_post_interaction=interaction_undo2,
    )

    # Process the fanout
    FanOutStates.handle_new(fanout2)

    # Verify the boost is now undone since post is not in timeline
    boost.refresh_from_db()
    assert boost.state == PostInteractionStates.undone


@pytest.mark.django_db
def test_group_actor_announce_includes_followers_cc(
    identity, group_identity, config_system
):
    """Test that a group actor's boost Announce includes its followers collection in cc"""
    group_identity.ensure_uris()
    group_identity.save()

    post = Post.create_local(
        author=identity,
        content="<p>Test post</p>",
    )

    boost = PostInteraction.objects.create(
        identity=group_identity,
        post=post,
        type=PostInteraction.Types.boost,
        published=timezone.now(),
    )

    ap = boost.to_ap()

    assert ap["type"] == "Announce"
    assert "cc" in ap
    assert group_identity.followers_uri in ap["cc"]


@pytest.mark.django_db
def test_non_group_actor_announce_has_no_cc(identity, config_system):
    """Test that a regular (non-group) user's boost Announce does not add followers cc"""
    Post.create_local(
        author=identity,
        content="<p>Test post</p>",
    )

    other_post = Post.create_local(
        author=identity,
        content="<p>Another post</p>",
    )

    boost = PostInteraction.objects.create(
        identity=identity,
        post=other_post,
        type=PostInteraction.Types.boost,
        published=timezone.now(),
    )

    ap = boost.to_ap()

    assert ap["type"] == "Announce"
    assert "cc" not in ap
