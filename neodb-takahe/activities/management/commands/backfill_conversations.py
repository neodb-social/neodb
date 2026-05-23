from django.core.management.base import BaseCommand

from activities.models import Post
from activities.models.conversation import Conversation, ConversationMembership


class Command(BaseCommand):
    help = (
        "Backfill conversations from existing direct-visibility posts. Safe to rerun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of posts to process per batch",
        )

    def handle(self, batch_size: int, *args, **options):
        direct_posts = (
            Post.objects.filter(visibility=Post.Visibilities.mentioned)
            .prefetch_related("mentions")
            .order_by("id")
        )
        total = direct_posts.count()
        self.stdout.write(f"Found {total} direct-visibility posts to process")

        created_convs = 0
        updated_posts = 0
        created_memberships = 0

        for post in direct_posts.iterator(chunk_size=batch_size):
            participant_ids = set(post.mentions.values_list("pk", flat=True))
            participant_ids.add(post.author_id)
            if len(participant_ids) < 2:
                continue

            h = Conversation.compute_participant_hash(participant_ids)
            conversation, created = Conversation.objects.get_or_create(
                participant_hash=h,
            )
            if created:
                conversation.participants.set(participant_ids)
                created_convs += 1

            # Only update post if not already assigned
            if post.conversation_id != conversation.pk:
                Post.objects.filter(pk=post.pk).update(conversation=conversation)
                updated_posts += 1

            # Update last_post if this is the newest (posts are ordered by id)
            if conversation.last_post_id is None or post.pk > conversation.last_post_id:
                conversation.last_post = post
                conversation.save(update_fields=["last_post", "updated"])

            # Create memberships (idempotent via get_or_create)
            for pid in participant_ids:
                _, created = ConversationMembership.objects.get_or_create(
                    identity_id=pid,
                    conversation=conversation,
                    defaults={"unread": False},
                )
                if created:
                    created_memberships += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_convs} conversations created, "
                f"{updated_posts} posts updated, "
                f"{created_memberships} memberships created"
            )
        )
