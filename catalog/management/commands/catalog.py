from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db.models import Count, F
from tqdm import tqdm

from catalog.models import Edition, Item, Podcast, TVSeason, TVShow
from common.models import detect_language, uniq


class Command(BaseCommand):
    help = "catalog app utilities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help="purge deleted items",
        )
        parser.add_argument(
            "--localize",
            action="store_true",
            help="migrate localized title/description",
        )
        parser.add_argument(
            "--integrity",
            action="store_true",
            help="check and fix integrity for merged and deleted items",
        )

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        if options["purge"]:
            self.purge()
        if options["integrity"]:
            self.integrity()
        if options["localize"]:
            self.localize()
        self.stdout.write(self.style.SUCCESS("Done."))

    def localize(self):
        c = Item.objects.all().count()
        qs = Item.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        for i in tqdm(qs.iterator(), total=c):
            localized_title = [{"lang": detect_language(i.title), "text": i.title}]
            if i.__class__ != Edition:
                if hasattr(i, "orig_title") and i.orig_title:  # type:ignore
                    localized_title += [
                        {
                            "lang": detect_language(i.orig_title),  # type:ignore
                            "text": i.orig_title,  # type:ignore
                        }
                    ]
                if hasattr(i, "other_title") and i.other_title:  # type:ignore
                    for title in i.other_title:  # type:ignore
                        localized_title += [
                            {"lang": detect_language(title), "text": title}
                        ]
            else:
                # Edition has no other_title
                subtitle = i.metadata.get("subtitle")
                i.metadata["localized_subtitle"] = (
                    [{"lang": detect_language(subtitle), "text": subtitle}]
                    if subtitle
                    else []
                )
                lang = i.metadata.get("language")
                if isinstance(lang, str) and lang:
                    i.metadata["language"] = [lang]
            if i.__class__ == Podcast and i.metadata.get("host", None) is None:
                i.metadata["host"] = i.metadata.get("hosts", [])
            i.localized_title = uniq(localized_title)
            localized_desc = [{"lang": detect_language(i.brief), "text": i.brief}]
            i.localized_description = localized_desc
            i.save(update_fields=["metadata"])

    def purge(self):
        for cls in Item.__subclasses__():
            if self.fix:
                self.stdout.write(f"Cleaning up {cls}...")
                cls.objects.filter(is_deleted=True).delete()

    def integrity(self):
        self.stdout.write("Checking circulated merge...")
        for i in Item.objects.filter(merged_to_item=F("id")):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                i.merged_to_item = None
                i.save()

        self.stdout.write("Checking chained merge...")
        for i in (
            Item.objects.filter(merged_to_item__isnull=False)
            .annotate(n=Count("merged_from_items"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for j in i.merged_from_items.all():
                    j.merged_to_item = i.merged_to_item
                    j.save()

        self.stdout.write("Checking deleted merge...")
        for i in Item.objects.filter(merged_to_item__isnull=False, is_deleted=True):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                i.is_deleted = False
                i.save()

        self.stdout.write("Checking deleted item with external resources...")
        for i in (
            Item.objects.filter(is_deleted=True)
            .annotate(n=Count("external_resources"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for r in i.external_resources.all():
                    r.item = None
                    r.save()

        self.stdout.write("Checking merged item with external resources...")
        for i in (
            Item.objects.filter(merged_to_item__isnull=False)
            .annotate(n=Count("external_resources"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for r in i.external_resources.all():
                    r.item = i.merged_to_item
                    r.save()

        tvshow_ct_id = ContentType.objects.get_for_model(TVShow).id
        self.stdout.write("Checking TVShow merged to other class...")
        for i in (
            TVShow.objects.filter(merged_to_item__isnull=False)
            .filter(merged_to_item__isnull=False)
            .exclude(merged_to_item__polymorphic_ctype_id=tvshow_ct_id)
        ):
            if i.child_items.all().exists():
                self.stdout.write(f"! with season {i} : {i.absolute_url}?skipcheck=1")
                if self.fix:
                    i.merged_to_item = None
                    i.save()
            else:
                self.stdout.write(f"! no season {i} : {i.absolute_url}?skipcheck=1")
                if self.fix:
                    i.recast_to(i.merged_to_item.__class__)  # type:ignore

        self.stdout.write("Checking TVSeason is child of other class...")
        for i in TVSeason.objects.filter(show__isnull=False).exclude(
            show__polymorphic_ctype_id=tvshow_ct_id
        ):
            if not i.show:
                continue
            self.stdout.write(f"! {i.show} : {i.show.absolute_url}?skipcheck=1")
            if self.fix:
                i.show = None
                i.save()

        self.stdout.write("Checking deleted item with child TV Season...")
        for i in TVSeason.objects.filter(show__is_deleted=True):
            if not i.show:
                continue
            self.stdout.write(f"! {i.show} : {i.show.absolute_url}?skipcheck=1")
            if self.fix:
                i.show.is_deleted = False
                i.show.save()

        self.stdout.write("Checking merged item with child TV Season...")
        for i in TVSeason.objects.filter(show__merged_to_item__isnull=False):
            if not i.show:
                continue
            self.stdout.write(f"! {i.show} : {i.show.absolute_url}?skipcheck=1")
            if self.fix:
                i.show = i.show.merged_to_item
                i.save()
