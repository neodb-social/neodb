import os
import shutil
import tempfile
from itertools import islice

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db.models import Count, Exists, Max, OuterRef

from catalog.models import *
from common.management.base import SiteCommand
from journal.models import *
from takahe.models import Identity, Post


class Command(SiteCommand):
    help = "generate sitemap.txt"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="write the sitemap to this local path instead of media storage, "
            "e.g. /www/root/sitemap.txt to serve it from the site root",
        )

    def handle(self, *args, **options):
        fd, temp = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        with open(temp, "w") as f:
            c = 50000
            # articles only count if the author has marked something,
            # so accounts created just to publish don't get listed
            has_marks = Exists(
                ShelfMember.objects.filter(owner_id=OuterRef("owner_id"))
            )
            for cl in [Collection, Review, Article]:
                if c <= 0:
                    break
                self.stdout.write(f"Collecting {cl.__name__}...")
                pcs = cl.objects.filter(
                    visibility=0, local=True, owner__anonymous_viewable=True
                )
                if cl is Article:
                    pcs = pcs.filter(has_marks)
                for p in pcs.iterator():
                    if c <= 0:
                        break
                    f.write(p.absolute_url + "\n")
                    c -= 1

            # comments have no page of their own; list the single post page
            # of recent ones, skipping posts rendered with a noindex tag
            self.stdout.write("Collecting Comments...")
            n = min(10000, c)
            comment_ids = (
                Comment.objects.filter(
                    visibility=0, local=True, owner__anonymous_viewable=True
                )
                .order_by("-created_time")
                .values_list("pk", flat=True)
                .iterator()
            )
            while n > 0:
                batch = list(islice(comment_ids, 1000))
                if not batch:
                    break
                post_ids = (
                    PiecePost.objects.filter(piece_id__in=batch)
                    .values("piece_id")
                    .annotate(latest=Max("post_id"))
                    .values_list("latest", flat=True)
                )
                posts = (
                    Post.objects.filter(
                        pk__in=list(post_ids),
                        local=True,
                        visibility=0,
                        author__restriction=Identity.Restriction.none,
                    )
                    .exclude(state__in=["deleted", "deleted_fanned_out"])
                    .select_related("author", "author__domain")
                    .order_by("-pk")
                )
                for post in posts[:n]:
                    f.write(post.absolute_object_uri() + "\n")
                    n -= 1
                    c -= 1

            self.stdout.write("Collecting Catalog Items...")
            ratings = (
                Rating.objects.values("item_id")
                .annotate(num=Count("item_id"))
                .filter(num__gte=5)
                .order_by("-num")[:c]
            )
            for r in ratings.iterator():
                f.write(Item.objects.get(pk=r["item_id"]).absolute_url + "\n")

        output = options.get("output")
        if output:
            parent = os.path.dirname(output)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.copyfile(temp, output)
            # mkstemp() creates the file as 0600, so set the mode the web
            # server expects
            os.chmod(output, 0o644)
            os.remove(temp)
            self.stdout.write(self.style.SUCCESS(f"Generated {output}"))
            return

        # Unlike an export, the sitemap is fetched by crawlers over the web, so
        # it has to live wherever MEDIA_URL actually points -- the bucket on an
        # S3 backend. Writing it to MEDIA_ROOT only worked while the two agreed.
        # It goes under its own prefix rather than export/, which a bucket
        # policy keeps private because the archives there hold account keys.
        rel = settings.PUBLIC_FILE_PATH_ROOT + "sitemap.txt"
        # FileSystemStorage.save() never overwrites; it would pick a suffixed
        # name and leave the advertised URL serving the previous run's file
        if default_storage.exists(rel):
            default_storage.delete(rel)
        with open(temp, "rb") as f:
            rel = default_storage.save(rel, File(f))
        os.remove(temp)
        url = default_storage.url(rel)
        if "://" not in url:
            url = settings.SITE_INFO["site_url"] + url
        self.stdout.write(self.style.SUCCESS(f"Generated {url}"))
