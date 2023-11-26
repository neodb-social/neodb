from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db.models import Count, F
from loguru import logger
from tqdm import tqdm

from catalog.common import *
from catalog.common.models import *
from catalog.models import *
from journal.models import *
from takahe.utils import *
from users.models import APIdentity
from users.models import User as NeoUser


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
        logger.info(f"Processing posts...")
        qs = Piece.objects.filter(
            polymorphic_ctype__in=[
                content_type_id(ShelfMember),
                content_type_id(Comment),
                content_type_id(Review),
            ]
        ).order_by("id")
        if self.starting_id:
            qs = qs.filter(id__gte=self.starting_id)
        tracker = tqdm(qs.iterator(), total=self.count_est or qs.count())
        for p in tracker:
            tracker.set_postfix_str(f"{p.id}")
            if p.__class__ == ShelfMember:
                mark = Mark(p.owner, p.item)
                Takahe.post_mark(mark, self.post_new)
            elif p.__class__ == Comment:
                if p.item.__class__ in [PodcastEpisode, TVEpisode]:
                    Takahe.post_comment(p, self.post_new)
            elif p.__class__ == Review:
                Takahe.post_review(p, self.post_new)

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

        if options["like"]:
            self.process_like()

        self.stdout.write(self.style.SUCCESS(f"Done."))
