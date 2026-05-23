import httpx
from django.db import models

from activities.models.timeline_event import TimelineEvent
from core.ld import canonicalise
from stator.models import State, StateField, StateGraph, StatorModel
from users.models import Block, FollowStates, Identity


class FanOutStates(StateGraph):
    new = State(try_interval=600)
    sent = State(delete_after=86400)
    skipped = State(delete_after=86400)
    failed = State(delete_after=86400)

    new.transitions_to(sent)
    new.transitions_to(skipped)
    new.times_out_to(failed, seconds=86400 * 3)

    @classmethod
    def _handle_group_actor_auto_boost(cls, identity: "Identity", post):
        """
        Handles automatic boosting for group actors
        """
        from activities.models import Post, PostInteraction, PostInteractionStates

        # Don't boost your own posts, or non-public posts
        if post.author_id == identity.id or post.visibility in [
            Post.Visibilities.followers,
            Post.Visibilities.mentioned,
        ]:
            return

        # Don't boost if this group already boosted this post
        if PostInteraction.objects.filter(
            identity=identity,
            post=post,
            type=PostInteraction.Types.boost,
            state__in=PostInteractionStates.group_active(),
        ).exists():
            return

        # Create a new boost interaction
        boost = PostInteraction.objects.create(
            identity=identity,
            post=post,
            type=PostInteraction.Types.boost,
        )
        boost.transition_perform(PostInteractionStates.new)

    @classmethod
    def _handle_group_actor_remove_boost(cls, identity: "Identity", post):
        """
        Removes any boosts by a group actor for a given post.
        """
        from activities.models import PostInteraction, PostInteractionStates

        boosts = PostInteraction.objects.filter(
            identity=identity,
            post=post,
            type=PostInteraction.Types.boost,
            state__in=PostInteractionStates.group_active(),
        )
        PostInteraction.transition_perform_queryset(
            boosts, PostInteractionStates.undone
        )

    @classmethod
    def handle_new(cls, instance: "FanOut"):
        """
        Sends the fan-out to the right inbox.
        """
        from activities.models import Post, PostInteraction

        # Don't try to fan out to identities that are not fetched yet
        if not (instance.identity.local or instance.identity.inbox_uri):
            return

        match (instance.type, instance.identity.local):
            # Handle creating/updating local posts
            case ((FanOut.Types.post | FanOut.Types.post_edited), True):
                post = instance.subject_post
                # If the author of the post is blocked or muted, skip out
                if (
                    Block.objects.active()
                    .filter(source=instance.identity, target=post.author)
                    .exists()
                ):
                    return cls.skipped
                # Make a timeline event directly
                # If it's a reply, we only add it if we follow at least one
                # of the people mentioned AND the author, or we're mentioned,
                # or it's a reply to us or the author
                add = True
                mentioned = {identity.id for identity in post.mentions.all()}
                if post.in_reply_to:
                    followed = set(
                        instance.identity.outbound_follows.filter(
                            state__in=FollowStates.group_active()
                        ).values_list("target_id", flat=True)
                    )
                    interested_in = followed.union(
                        {post.author_id, instance.identity_id}
                    )
                    add = (post.author_id in followed) and (
                        bool(mentioned.intersection(interested_in))
                    )
                if add:
                    TimelineEvent.add_post(
                        identity=instance.identity,
                        post=post,
                    )
                # We might have been mentioned
                if (
                    instance.identity.id in mentioned
                    and instance.identity_id != post.author_id
                ):
                    TimelineEvent.add_mentioned(
                        identity=instance.identity,
                        post=post,
                    )
                # We might have been quoted
                if post.quote_url:
                    quoted_post = (
                        Post.objects.filter(object_uri=post.quote_url)
                        .only("pk", "author_id")
                        .first()
                    )
                    if (
                        quoted_post
                        and quoted_post.author_id == instance.identity_id
                        and instance.identity_id != post.author_id
                    ):
                        TimelineEvent.add_quoted(
                            identity=instance.identity,
                            post=post,
                        )

                # Handle group actor automatic boosting
                if instance.identity.is_group:
                    cls._handle_group_actor_auto_boost(instance.identity, post)

            # Handle sending remote posts create
            case (FanOut.Types.post, False):
                post = instance.subject_post
                # Sign it and send it
                try:
                    post.author.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(post.to_create_ap()),
                    )
                except httpx.RequestError:
                    return

            # Handle sending remote posts update
            case (FanOut.Types.post_edited, False):
                post = instance.subject_post
                # Sign it and send it
                try:
                    post.author.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(post.to_update_ap()),
                    )
                except httpx.RequestError:
                    return

            # Handle deleting local posts
            case (FanOut.Types.post_deleted, True):
                if instance.identity.is_group:
                    cls._handle_group_actor_remove_boost(
                        instance.identity, instance.subject_post
                    )

            # Handle sending remote post deletes
            case (FanOut.Types.post_deleted, False):
                post = instance.subject_post
                # Send it to the remote inbox
                try:
                    post.author.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(post.to_delete_ap()),
                    )
                except ValueError:
                    pass  # ignore 401 when identity deletion is processed by remote earlier
                except httpx.RequestError:
                    return

            # Handle local boosts/likes
            case (FanOut.Types.interaction, True):
                interaction = instance.subject_post_interaction
                # If the author of the interaction is blocked or their notifications
                # are muted, skip out
                if (
                    Block.objects.active()
                    .filter(
                        models.Q(mute=False) | models.Q(include_notifications=True),
                        source=instance.identity,
                        target=interaction.identity,
                    )
                    .exists()
                ):
                    return cls.skipped
                # If blocked/muted the underlying post author, skip out
                if (
                    Block.objects.active()
                    .filter(
                        source=instance.identity,
                        target_id=interaction.post.author_id,
                    )
                    .exists()
                ):
                    return cls.skipped
                # Make a timeline event directly
                TimelineEvent.add_post_interaction(
                    identity=instance.identity,
                    interaction=interaction,
                )

                # If this is a boost and the identity is a group, also boost the post
                if (
                    instance.identity.is_group
                    and interaction.type == PostInteraction.Types.boost
                ):
                    cls._handle_group_actor_auto_boost(
                        instance.identity, interaction.post
                    )

            # Handle sending remote boosts/likes/votes/pins
            case (FanOut.Types.interaction, False):
                interaction = instance.subject_post_interaction
                # Send it to the remote inbox
                try:
                    if interaction.type == interaction.Types.vote:
                        body = interaction.to_create_ap()
                    elif interaction.type == interaction.Types.pin:
                        body = interaction.to_add_ap()
                    else:
                        body = interaction.to_ap()
                    interaction.identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(body),
                    )
                except httpx.RequestError:
                    return

            # Handle undoing local boosts/likes
            case (FanOut.Types.undo_interaction, True):  # noqa:F841
                interaction = instance.subject_post_interaction

                # Delete any local timeline events
                TimelineEvent.delete_post_interaction(
                    identity=instance.identity,
                    interaction=interaction,
                )

                # If this is a boost and the identity is a group
                # and the post itself is not in the group's timeline
                if (
                    interaction.type == PostInteraction.Types.boost
                    and instance.identity.is_group
                    and not TimelineEvent.objects.filter(
                        identity=instance.identity,
                        subject_post=instance.subject_post,
                        type__in=[
                            TimelineEvent.Types.post,
                            TimelineEvent.Types.mentioned,
                        ],
                    ).exists()
                ):
                    cls._handle_group_actor_remove_boost(
                        instance.identity, interaction.post
                    )

            # Handle sending remote undoing boosts/likes/pins
            case (FanOut.Types.undo_interaction, False):  # noqa:F841
                interaction = instance.subject_post_interaction
                # Send an undo to the remote inbox
                try:
                    if interaction.type == interaction.Types.pin:
                        body = interaction.to_remove_ap()
                    else:
                        body = interaction.to_undo_ap()
                    interaction.identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(body),
                    )
                except httpx.RequestError:
                    return

            # Handle sending identity edited to remote
            case (FanOut.Types.identity_edited, False):
                identity = instance.subject_identity
                try:
                    identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(instance.subject_identity.to_update_ap()),
                    )
                except httpx.RequestError:
                    return

            # Handle sending identity deleted to remote
            case (FanOut.Types.identity_deleted, False):
                identity = instance.subject_identity
                try:
                    identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(instance.subject_identity.to_delete_ap()),
                    )
                except httpx.RequestError:
                    return
                except ValueError:
                    pass  # do not retry if 4xx

            # Handle move for local follower
            case (FanOut.Types.identity_moved, True):
                from users.services import IdentityService

                identity = instance.subject_identity
                if identity.has_moved() and identity.aliases:
                    follower = IdentityService(instance.identity)
                    new_identity = Identity.by_actor_uri(identity.aliases[0])
                    follower.unfollow(identity)
                    follower.follow(new_identity)

            # Handle sending identity moved to remote
            case (FanOut.Types.identity_moved, False):
                identity = instance.subject_identity
                if identity.has_moved() and identity.aliases:
                    try:
                        identity.signed_request(
                            method="post",
                            uri=(
                                instance.identity.shared_inbox_uri
                                or instance.identity.inbox_uri
                            ),
                            body=canonicalise(identity.to_move_ap()),
                        )
                    except httpx.RequestError:
                        return

            # Sending identity edited/deleted to local is a no-op
            case (FanOut.Types.identity_edited, True):
                pass
            case (FanOut.Types.identity_deleted, True):
                pass

            # Created identities make a timeline event
            case (FanOut.Types.identity_created, True):
                TimelineEvent.add_identity_created(
                    identity=instance.identity,
                    new_identity=instance.subject_identity,
                )

            case (FanOut.Types.tag_featured, True):
                pass

            case (FanOut.Types.tag_featured, False):
                identity = instance.subject_identity
                try:
                    identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(instance.subject_hashtag.to_add_ap(identity)),
                    )
                except httpx.RequestError:
                    return

            case (FanOut.Types.tag_unfeatured, True):
                pass

            case (FanOut.Types.tag_unfeatured, False):
                identity = instance.subject_identity
                try:
                    identity.signed_request(
                        method="post",
                        uri=(
                            instance.identity.shared_inbox_uri
                            or instance.identity.inbox_uri
                        ),
                        body=canonicalise(
                            instance.subject_hashtag.to_remove_ap(identity)
                        ),
                    )
                except httpx.RequestError:
                    return

            case _:
                raise ValueError(
                    f"Cannot fan out with type {instance.type} local={instance.identity.local}"
                )

        return cls.sent


class FanOut(StatorModel):
    """
    An activity that needs to get to an inbox somewhere.
    """

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
        tag_featured = "tag_featured"
        tag_unfeatured = "tag_unfeatured"

    state = StateField(FanOutStates)

    # The user this event is targeted at
    # We always need this, but if there is a shared inbox URL on the user
    # we'll deliver to that and won't have fanouts for anyone else with the
    # same one.
    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="fan_outs",
    )

    # What type of activity it is
    type = models.CharField(max_length=100, choices=Types.choices)

    # Links to the appropriate objects
    subject_post = models.ForeignKey(
        "activities.Post",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="fan_outs",
    )
    subject_post_interaction = models.ForeignKey(
        "activities.PostInteraction",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="fan_outs",
    )
    subject_identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="subject_fan_outs",
    )
    subject_hashtag = models.ForeignKey(
        "activities.Hashtag",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="fan_outs",
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
