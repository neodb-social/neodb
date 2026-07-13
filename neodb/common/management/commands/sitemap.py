import os
import shutil
import tempfile

from django.conf import settings
from django.db.models import Count, Exists, OuterRef

from catalog.models import *
from common.management.base import SiteCommand
from journal.models import *


class Command(SiteCommand):
    help = "generate sitemap.txt"

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

            self.stdout.write("Collecting Catalog Items...")
            ratings = (
                Rating.objects.values("item_id")
                .annotate(num=Count("item_id"))
                .filter(num__gte=5)
                .order_by("-num")[:c]
            )
            for r in ratings.iterator():
                f.write(Item.objects.get(pk=r["item_id"]).absolute_url + "\n")

        fn = settings.MEDIA_ROOT + "/" + settings.EXPORT_FILE_PATH_ROOT + "sitemap.txt"
        shutil.copy2(temp, fn)
        url = (
            settings.SITE_INFO["site_url"]
            + settings.MEDIA_URL
            + settings.EXPORT_FILE_PATH_ROOT
            + "sitemap.txt"
        )
        self.stdout.write(self.style.SUCCESS(f"Generated {url}"))
