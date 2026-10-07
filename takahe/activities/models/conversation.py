import hashlib

from django.db import models

from api.streaming_events import publish
from core.snowflake import Snowflake


class Conversation(models.Model):
    """
    A direct message conversation between a set of participants.
    """

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_post)

    # SHA-256 hash of sorted participant Identity IDs for fast lookup
    participant_hash = models.CharField(max_length=64, unique=True)

    # The participants in this conversation
    participants = models.ManyToManyField(
        "users.Identity",
        related_name="conversations",
        blank=True,
    )

    # Denormalized: most recent direct post in the conversation
    last_post = models.ForeignKey(
        "activities.Post",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Sent as both `context` and `conversation` on outbound posts. Ours when a
    # local post started the conversation, otherwise the first one a peer sent.
    # Not unique: a remote reply that drops a mention keeps the peer's context
    # but forms a different participant set here, as it does on Mastodon,
    # GoToSocial and Pixelfed.
    uri = models.CharField(max_length=500, blank=True, null=True, db_index=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["updated"]),
        ]

    @staticmethod
    def local_uri_for(conversation_id: int, actor_uri: str) -> str:
        return f"{actor_uri}conversations/{conversation_id}/"

    @staticmethod
    def remote_uri_from(data: dict) -> str | None:
        """
        The conversation URI a peer sent with a post: `conversation` first,
        since Mastodon threads root posts by it, then `context`.
        """
        from core.ld import get_str_or_id

        for key in ("conversation", "context"):
            value = get_str_or_id(data.get(key))
            if (
                value
                and len(value) <= 500
                and value.split(":", 1)[0].lower() in ("https", "http", "tag")
            ):
                return value
        return None

    @staticmethod
    def compute_participant_hash(identity_ids: set[int]) -> str:
        """Compute a deterministic hash from a set of participant Identity PKs."""
        sorted_ids = sorted(identity_ids)
        raw = ",".join(str(i) for i in sorted_ids)
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def get_or_create_for_participants(cls, identity_ids: set[int]) -> "Conversation":
        """Find or create a conversation for the exact set of participants."""
        h = cls.compute_participant_hash(identity_ids)
        conversation, created = cls.objects.get_or_create(
            participant_hash=h,
        )
        if created:
            from users.models import Identity

            conversation.participants.set(Identity.objects.filter(pk__in=identity_ids))
        return conversation

    @classmethod
    def update_for_post(
        cls, post: "models.Model", remote_uri: str | None = None
    ) -> None:
        """
        Called after a direct-visibility post is saved to assign it to a
        conversation and update membership state.

        remote_uri is the conversation URI an inbound post carried.
        """
        from activities.models.post import Post

        if post.visibility != Post.Visibilities.mentioned:
            return
        participant_ids = set(post.mentions.values_list("pk", flat=True))
        participant_ids.add(post.author_id)
        if len(participant_ids) < 2:
            return
        conversation = cls.get_or_create_for_participants(participant_ids)
        Post.objects.filter(pk=post.pk).update(conversation=conversation)
        post.conversation = conversation
        update_fields = []
        if not conversation.uri:
            if post.local:
                conversation.uri = cls.local_uri_for(
                    conversation.pk, post.author.actor_uri
                )
            else:
                conversation.uri = remote_uri
            if conversation.uri:
                update_fields.append("uri")
        # Update last_post if this post is newer
        if conversation.last_post_id is None or post.pk > conversation.last_post_id:
            conversation.last_post = post
            update_fields.append("last_post")
        if update_fields:
            conversation.save(update_fields=update_fields + ["updated"])
        # Create/update memberships
        for pid in participant_ids:
            is_author = pid == post.author_id
            membership, created = ConversationMembership.objects.get_or_create(
                identity_id=pid,
                conversation=conversation,
                defaults={"unread": not is_author},
            )
            if created:
                continue
            updates = []
            # Writing in a conversation means having read it, as on Mastodon
            if membership.unread != (not is_author):
                membership.unread = not is_author
                updates.append("unread")
            if membership.dismissed:
                membership.dismissed = False
                updates.append("dismissed")
            if updates:
                membership.save(update_fields=updates + ["updated"])

        for pid in participant_ids:
            publish(f"user:{pid}", {"kind": "conversation", "id": conversation.pk})


class ConversationMembership(models.Model):
    """Per-identity state for a conversation."""

    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="conversation_memberships",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    unread = models.BooleanField(default=True)
    dismissed = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("identity", "conversation")]
        indexes = [
            models.Index(fields=["identity", "dismissed"]),
        ]
