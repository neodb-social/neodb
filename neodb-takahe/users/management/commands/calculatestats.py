from django.core.management.base import BaseCommand
from django.db.models import (
    Count,
    DateTimeField,
    IntegerField,
    Max,
    OuterRef,
    Subquery,
)

from activities.models import Post
from users.models import Follow, Identity


class Command(BaseCommand):
    help = "Recalculates Identity stats"

    def handle(self, *args, **options):
        posts = (
            Post.objects.filter(author_id=OuterRef("id"))
            .values("author_id")
            .annotate(num=Count("id"))
            .values("num")[:1]
        )
        latest = (
            Post.objects.filter(author_id=OuterRef("id"))
            .values("author_id")
            .annotate(latest=Max("created"))
            .values("latest")[:1]
        )
        followers = (
            Follow.objects.filter(target_id=OuterRef("id"))
            .values("target_id")
            .annotate(num=Count("id"))
            .values("num")[:1]
        )
        following = (
            Follow.objects.filter(source_id=OuterRef("id"))
            .values("source_id")
            .annotate(num=Count("id"))
            .values("num")[:1]
        )

        qs = Identity.objects.annotate(
            statuses_count=Subquery(posts, output_field=IntegerField()),
            last_status_at=Subquery(latest, output_field=DateTimeField()),
            followers_count=Subquery(followers, output_field=IntegerField()),
            following_count=Subquery(following, output_field=IntegerField()),
        )

        for i in qs:
            latest = i.last_status_at.date().isoformat() if i.last_status_at else None
            i.stats = {
                "statuses_count": i.statuses_count or 0,
                "last_status_at": latest,
                "followers_count": i.followers_count or 0,
                "following_count": i.following_count or 0,
            }
            i.save(update_fields=["stats"])
