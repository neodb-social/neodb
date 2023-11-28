from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db.models import Count, F
from loguru import logger
from tqdm import tqdm

from catalog.common import *
from catalog.common.models import *
from catalog.models import *
from journal.models import *
from takahe.models import Identity as TakaheIdentity
from takahe.models import Post as TakahePost
from takahe.models import TimelineEvent, set_disable_timeline
from takahe.utils import *
from users.models import APIdentity
from users.models import User as NeoUser

BATCH_SIZE = 1000


def content_type_id(cls):
    return ContentType.objects.get(app_label="journal", model=cls.__name__.lower()).pk


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--post",
            action="store_true",
        )
        parser.add_argument(
            "--timeline",
            action="store_true",
        )
        parser.add_argument(
            "--like",
            action="store_true",
        )
        parser.add_argument(
            "--post-new",
            action="store_true",
        )
        parser.add_argument("--start", default=0, action="store")
        parser.add_argument("--count", default=0, action="store")

    def process_post(self):
        logger.info(f"Generating posts...")
        set_disable_timeline(True)
        qs = Piece.objects.filter(
            polymorphic_ctype__in=[
                content_type_id(ShelfMember),
                content_type_id(Comment),
                content_type_id(Review),
            ]
        ).order_by("id")
        if self.starting_id:
            qs = qs.filter(id__gte=self.starting_id)
        pg = Paginator(qs, BATCH_SIZE)
        tracker = tqdm(pg.page_range)
        for page in tracker:
            with transaction.atomic(using="default"):
                with transaction.atomic(using="takahe"):
                    for p in pg.page(page):
                        tracker.set_postfix_str(f"{p.id}")
                        if p.__class__ == ShelfMember:
                            mark = Mark(p.owner, p.item)
                            Takahe.post_mark(mark, self.post_new)
                        elif p.__class__ == Comment:
                            if p.item.__class__ in [PodcastEpisode, TVEpisode]:
                                Takahe.post_comment(p, self.post_new)
                        elif p.__class__ == Review:
                            Takahe.post_review(p, self.post_new)
        set_disable_timeline(False)

    def process_timeline(self):
        def add_event(post_id, author_id, owner_id, published):
            TimelineEvent.objects.get_or_create(
                identity_id=owner_id,
                type="post",
                subject_post_id=post_id,
                subject_identity_id=author_id,
                defaults={
                    "published": published,
                },
            )

        logger.info(f"Generating cache for timeline...")
        followers = {
            apid.pk: apid.followers if apid.is_active else []
            for apid in APIdentity.objects.filter(local=True)
        }
        cnt = TakahePost.objects.count()
        qs = TakahePost.objects.filter(local=True).order_by("published")
        pg = Paginator(qs, BATCH_SIZE)
        logger.info(f"Generating timeline...")
        for p in tqdm(pg.page_range):
            with transaction.atomic(using="takahe"):
                posts = pg.page(p)
                events = []
                for post in posts:
                    events.append(
                        TimelineEvent(
                            identity_id=post.author_id,
                            type="post",
                            subject_post_id=post.pk,
                            subject_identity_id=post.author_id,
                            published=post.published,
                        )
                    )
                    if post.visibility != 3:
                        for follower_id in followers[post.author_id]:
                            events.append(
                                TimelineEvent(
                                    identity_id=follower_id,
                                    type="post",
                                    subject_post_id=post.pk,
                                    subject_identity_id=post.author_id,
                                    published=post.published,
                                )
                            )
                TimelineEvent.objects.bulk_create(events, ignore_conflicts=True)
                # for post in posts:
                #     add_event(post.pk, post.author_id, post.author_id, post.published)
                #     if post.visibility != 3:
                #         for follower_id in followers[post.author_id]:
                #             add_event(
                #                 post.pk, post.author_id, follower_id, post.published
                #             )

    def process_like(self):
        logger.info(f"Processing likes...")
        qs = Like.objects.order_by("id")
        tracker = tqdm(qs)
        for like in tracker:
            post_id = like.target.latest_post_id
            if post_id:
                Takahe.like_post(post_id, like.owner.pk)
            else:
                logger.warning(f"Post not found for like {like.id}")

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.post_new = options["post_new"]
        self.starting_id = int(options["start"])
        self.count_est = int(options["count"])

        if options["post"]:
            self.process_post()

        if options["timeline"]:
            self.process_timeline()

        if options["like"]:
            self.process_like()

        self.stdout.write(self.style.SUCCESS(f"Done."))
