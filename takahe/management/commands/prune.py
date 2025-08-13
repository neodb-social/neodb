import datetime
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from tqdm import tqdm

from takahe.models import Domain, Post
from takahe.utils import Takahe


class Command(BaseCommand):
    help = "Prunes posts that are old, not local and have no local interaction"

    def add_arguments(self, parser):
        parser.add_argument(
            "--number",
            "-n",
            type=int,
            default=1000,
            help="The maximum number of posts to prune at once",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Perform a dry run without deleting any posts",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show posts to delete",
        )

    def handle(self, number: int, dry_run: bool, verbose: bool, *args, **options):
        horizon = settings.REMOTE_PRUNE_HORIZON
        if not horizon:
            self.stdout.write(self.style.WARNING("Pruning has been disabled"))
            sys.exit(0)
        locs = Domain.objects.filter(local=True).values_list("pk", flat=True)
        remote_peers = Takahe.get_neodb_peers()
        all_nodes = remote_peers + list(locs)
        self.stdout.write(f"Prune up to {number} posts older than {horizon} days.")
        if verbose:
            self.stdout.write("Excluding ones that are local...")
            self.stdout.write("Excluding ones that has replies...")
            self.stdout.write("Excluding ones that are replies to local posts...")
            self.stdout.write(f"Excluding ones from: {' '.join(remote_peers)} ...")
            self.stdout.write("Finding posts...", ending="")
        num = number
        c = 1
        t = tqdm(total=number)
        while c > 0 and num > 0:
            n = min(num, 1000)
            c = self.run_once(n, dry_run, verbose, horizon, all_nodes)
            t.update(c)
            num -= c
        t.close()
        sys.exit(1 if c > 0 else 0)
        # exit 1 if more to delete so the job may retry to delete more

    def run_once(
        self,
        number: int,
        dry_run: bool,
        verbose: bool,
        horizon: int,
        all_nodes: list[str],
    ) -> int:
        # Find a set of posts that match the initial criteria

        posts = (
            Post.objects.filter(
                local=False,
                created__lt=timezone.now() - datetime.timedelta(days=horizon),
            )
            .exclude(author__domain__in=all_nodes)
            .exclude(
                Q(interactions__identity__local=True)
                | Q(visibility=Post.Visibilities.mentioned)
            )
            .exclude(
                Exists(
                    Post.objects.filter(
                        in_reply_to__isnull=False,
                        in_reply_to=OuterRef("object_uri"),
                    )
                )
            )
            .exclude(
                Exists(
                    Post.objects.filter(
                        object_uri__isnull=False,
                        object_uri=OuterRef("in_reply_to"),
                        author__domain__in=all_nodes,
                    )
                )
            )
            .order_by("?")[:number]
        )
        post_ids = list(posts.values_list("pk", flat=True))
        if verbose:
            self.stdout.write(self.style.SUCCESS(f"Found {len(post_ids)} posts"))

        if verbose:
            for p in posts:
                self.stdout.write(f"{p.pk} {p.author} {p.object_uri} {p.content}")

        if not post_ids or dry_run:
            return 0

        if verbose:
            self.stdout.write("Deleting...", ending="")
        _, deleted = Post.objects.filter(pk__in=post_ids).delete()

        if verbose:
            self.stdout.write(self.style.SUCCESS("Done."))
            for model, model_deleted in deleted.items():
                self.stdout.write(f"  - {model}: {model_deleted}")
        return len(post_ids)
