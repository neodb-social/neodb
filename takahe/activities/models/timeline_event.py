from django.db import OperationalError, models
from django.utils import timezone

from api.models.push import PushType
from core.ld import format_ld_date
from stator.exceptions import TryAgainLater
from users.models import Bookmark, Identity


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
        quoted = "quoted"  # Someone quoting one of our posts
        announcement = "announcement"  # Server announcement
        identity_created = "identity_created"  # New identity created
        poll = "poll"  # A poll we created or voted in has ended

    NOTIFICATION_NAMES = {
        Types.post: "status",
        Types.liked: "favourite",
        Types.boosted: "reblog",
        Types.mentioned: "mention",
        Types.followed: "follow",
        Types.follow_requested: "follow_request",
        Types.quoted: "quote",
        Types.identity_created: "admin.sign_up",
        Types.poll: "poll",
    }

    # The user this event is for
    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="timeline_events",
    )

    # What type of event it is
    type = models.CharField(max_length=100, choices=Types.choices)

    # The subject of the event (which is used depends on the type)
    subject_post = models.ForeignKey(
        "activities.Post",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="timeline_events",
    )
    subject_post_interaction = models.ForeignKey(
        "activities.PostInteraction",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="timeline_events",
    )
    subject_identity = models.ForeignKey(
        "users.Identity",
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
            # Supports the Mastodon /api/v1/notifications paginator which
            # filters by identity + dismissed=false and orders by id DESC.
            models.Index(
                fields=["identity", "-id"],
                condition=models.Q(dismissed=False),
                name="te_identity_idneg_undismissed",
            ),
        ]

    ### Alternate constructors ###

    @classmethod
    def add_follow(cls, identity, source_identity):
        """
        Adds a follow to the timeline if it's not there already, remove follow request if any
        """
        cls.objects.filter(
            type=cls.Types.follow_requested,
            identity=identity,
            subject_identity=source_identity,
        ).delete()
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.followed,
            subject_identity=source_identity,
        )
        if created:
            identity.notify(PushType.follow, source_identity)
        return event

    @classmethod
    def add_follow_request(cls, identity, source_identity):
        """
        Adds a follow request to the timeline if it's not there already
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.follow_requested,
            subject_identity=source_identity,
        )
        if created:
            identity.notify(PushType.follow_request, source_identity)
        return event

    @classmethod
    def add_post(cls, identity, post):
        """
        Adds a post to the timeline if it's not there already
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.post,
            subject_post=post,
            subject_identity=post.author,
            defaults={"published": post.published or post.created},
        )
        if created:
            # Only send the push notification if following with the notify flag set.
            if (
                identity.outbound_follows.active()
                .filter(target=post.author, notify=True)
                .exists()
            ):
                identity.notify(
                    PushType.status, post.author, body=post.content_preview()
                )
        return event

    @classmethod
    def add_mentioned(cls, identity, post):
        """
        Adds a mention of identity by post
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.mentioned,
            subject_post=post,
            subject_identity=post.author,
            defaults={"published": post.published or post.created},
        )
        if created:
            identity.notify(PushType.mention, post.author, body=post.content_preview())
        return event

    @classmethod
    def add_quoted(cls, identity, post):
        """
        Adds a quote notification when someone quotes one of our posts
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.quoted,
            subject_post=post,
            subject_identity=post.author,
            defaults={"published": post.published or post.created},
        )
        if created:
            identity.notify(PushType.quote, post.author, body=post.content_preview())
        return event

    @classmethod
    def add_poll_ended(cls, identity, post):
        """
        Notifies identity that a poll they created or voted in has ended
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.poll,
            subject_post=post,
            subject_identity=post.author,
        )
        if created:
            title = (
                "Your poll has ended"
                if identity == post.author
                else "A poll you voted in has ended"
            )
            identity.notify(
                PushType.poll, post.author, title=title, body=post.content_preview()
            )
        return event

    @classmethod
    def add_identity_created(cls, identity, new_identity):
        """
        Adds a new identity item
        """
        event, created = cls.objects.get_or_create(
            identity=identity,
            type=cls.Types.identity_created,
            subject_identity=new_identity,
        )
        if created:
            identity.notify(PushType.admin_signup, new_identity)
        return event

    @classmethod
    def add_post_interaction(cls, identity, interaction):
        """
        Adds a boost/like to the timeline if it's not there already.

        For boosts, may make two objects - one "boost" and one "boosted".
        It'll return the "boost" in that case.
        """
        if interaction.type == interaction.Types.like:
            event, created = cls.objects.get_or_create(
                identity=identity,
                type=cls.Types.liked,
                subject_post_id=interaction.post_id,
                subject_identity_id=interaction.identity_id,
                subject_post_interaction=interaction,
            )
            if created:
                identity.notify(
                    PushType.favorite,
                    interaction.identity,
                    body=interaction.post.content_preview(),
                )
            return event
        elif interaction.type == interaction.Types.boost:
            # If the boost is on one of our posts, then that's a boosted too
            boost_type = (
                cls.Types.boosted
                if interaction.post.author_id == identity.id
                else cls.Types.boost
            )
            event, created = cls.objects.get_or_create(
                identity=identity,
                type=boost_type,
                subject_post_id=interaction.post_id,
                subject_identity_id=interaction.identity_id,
                subject_post_interaction=interaction,
            )
            # Only send notifications for boosts of our posts.
            if created and boost_type == cls.Types.boosted:
                identity.notify(
                    PushType.boost,
                    interaction.identity,
                    body=interaction.post.content_preview(),
                )
            return event

    @classmethod
    def delete_post_interaction(cls, identity, interaction):
        if interaction.type == interaction.Types.like:
            cls.objects.filter(
                identity=identity,
                type=cls.Types.liked,
                subject_post_id=interaction.post_id,
                subject_identity_id=interaction.identity_id,
            ).delete()
        elif interaction.type == interaction.Types.boost:
            cls.objects.filter(
                identity=identity,
                type__in=[cls.Types.boosted, cls.Types.boost],
                subject_post_id=interaction.post_id,
                subject_identity_id=interaction.identity_id,
            ).delete()

    @classmethod
    def delete_follow(cls, target, source):
        TimelineEvent.objects.filter(
            type__in=[cls.Types.followed, cls.Types.follow_requested],
            identity=target,
            subject_identity=source,
        ).delete()

    ### Background tasks ###

    @classmethod
    def handle_clear_timeline(cls, message):
        """
        Internal stator handler for clearing all events by a user off another
        user's timeline.
        """
        from activities.models.post_interaction import (
            PostInteraction,
            PostInteractionStates,
        )

        actor_id = message["actor"]
        object_id = message["object"]
        full_erase = message.get("fullErase", False)

        if full_erase:
            q = (
                models.Q(subject_post__author_id=object_id)
                | models.Q(subject_post_interaction__identity_id=object_id)
                | models.Q(subject_identity_id=object_id)
            )
        else:
            q = models.Q(
                type=cls.Types.post, subject_post__author_id=object_id
            ) | models.Q(type=cls.Types.boost, subject_identity_id=object_id)
        try:
            TimelineEvent.objects.filter(q, identity_id=actor_id).delete()
            if full_erase:
                Bookmark.objects.filter(
                    identity_id=actor_id, post__author_id=object_id
                ).delete()
                Bookmark.objects.filter(
                    identity_id=actor_id, post__author_id=object_id
                ).delete()
                PostInteraction.objects.filter(
                    identity=actor_id, post__author=object_id
                ).update(state=PostInteractionStates.undone)
                PostInteraction.objects.filter(
                    identity=object_id, post__author=actor_id
                ).update(state=PostInteractionStates.undone)
                actor = Identity.objects.filter(pk=actor_id).first()
                if actor:
                    for post in actor.posts_mentioning.filter(author_id=object_id):
                        post.mentions.remove(actor)
                        parent = post.in_reply_to_post()
                        if parent and parent.author_id == actor_id:
                            # recalculate reply count
                            parent.calculate_stats()
        except OperationalError as e:
            # Concurrent deletes on activities_timelineevent (e.g. a Post delete
            # cascade racing another ClearTimeline) can deadlock. Re-raise as
            # TryAgainLater so Stator silently reschedules.
            if "deadlock detected" not in str(e):
                raise
            raise TryAgainLater() from e

    ### Mastodon Client API ###

    def to_mastodon_notification_json(self, interactions=None):
        if self.type not in TimelineEvent.NOTIFICATION_NAMES:
            raise ValueError(f"Cannot convert {self.type} to notification JSON")
        result = {
            "id": str(self.pk),
            "group_key": "ungrouped-" + str(self.pk),
            "created_at": format_ld_date(self.created),
            "account": self.subject_identity.to_mastodon_json(),
            "type": TimelineEvent.NOTIFICATION_NAMES[self.type],
        }
        if self.subject_post:
            result["status"] = self.subject_post.to_mastodon_json(
                interactions=interactions
            )
        return result

    def to_mastodon_status_json(self, interactions=None, bookmarks=None, identity=None):
        if self.type == self.Types.post:
            return self.subject_post.to_mastodon_json(
                interactions=interactions, bookmarks=bookmarks, identity=identity
            )
        elif self.type == self.Types.boost:
            return self.subject_post_interaction.to_mastodon_status_json(
                interactions=interactions, identity=identity
            )
        else:
            raise ValueError(f"Cannot make status JSON for type {self.type}")
