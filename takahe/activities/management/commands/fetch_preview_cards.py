import datetime

from core.html import FediverseHtmlParser
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from activities.models import Post
from activities.models.post import _attach_preview_card
from activities.models.preview_card import PreviewCard, PreviewCardStates


class Command(BaseCommand):
    help = (
        "Backfill preview cards for posts published in the last N days. Safe to rerun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            required=True,
            help="Process posts published within this many days",
        )

    def handle(self, days: int, *args, **options):
        since = timezone.now() - datetime.timedelta(days=days)
        posts = (
            Post.objects.filter(published__gte=since)
            .select_related("preview_card")
            .order_by("published")
        )
        total = posts.count()
        self.stdout.write(
            f"Scanning {total} posts published in the last {days} days..."
        )

        created = requeued = skipped = 0

        for post in posts.iterator():
            with transaction.atomic():
                card = post.preview_card

                if card is not None:
                    if card.state == PreviewCardStates.fetched:
                        skipped += 1
                        continue
                    if card.state == PreviewCardStates.needs_fetch:
                        skipped += 1
                        continue
                    if card.state == PreviewCardStates.fetch_failed:
                        # Reset to needs_fetch for explicit retry
                        card.transition_perform(PreviewCardStates.needs_fetch)
                        PreviewCard.objects.filter(pk=card.pk).update(
                            last_referenced_at=timezone.now()
                        )
                        requeued += 1
                        continue

                # No card yet — extract URL and create
                matches = FediverseHtmlParser.URL_REGEX.findall(post.content or "")
                url = matches[0].lstrip("(") if matches else None
                if not url or not url.startswith(("http://", "https://")):
                    skipped += 1
                    continue

                _attach_preview_card(post.pk, post.content)
                created += 1

        self.stdout.write(
            f"Done. Created: {created}, Re-queued: {requeued}, Skipped: {skipped}"
        )
