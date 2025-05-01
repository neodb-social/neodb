import json
import time

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db.models import Count, F
from tqdm import tqdm

from catalog.common.sites import SiteManager
from catalog.index import CatalogIndex, CatalogQueryParser
from catalog.models import (
    Edition,
    Item,
    Podcast,
    TVSeason,
    TVShow,
)
from catalog.search.external import ExternalSources
from catalog.sites.fedi import FediverseInstance
from common.models import detect_language, uniq

_CONFIRM = "confirm deleting collection? [Y/N] "
_HELP_TEXT = """
integrity:      check and fix integrity for merged and deleted items
purge:          purge deleted items
migrate:        run migration
search:         search docs in index
extsearch:      search external sites
idx-info:       show index information
idx-init:       check and create index if not exists
idx-destroy:    delete index
idx-alt:        update index schema
idx-delete:     delete docs in index
idx-reindex:    reindex docs
idx-get:        dump one doc with --url
"""


class Command(BaseCommand):
    help = "catalog app utilities"

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=[
                "integrity",
                "purge",
                "migrate",
                "search",
                "extsearch",
                "idx-info",
                "idx-init",
                "idx-alt",
                "idx-destroy",
                "idx-reindex",
                "idx-delete",
                "idx-get",
            ],
            help=_HELP_TEXT,
        )
        parser.add_argument(
            "--category",
            default="all",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
        )
        parser.add_argument(
            "--query",
        )
        parser.add_argument(
            "--url",
        )
        parser.add_argument(
            "--batch-size",
            default=1000,
        )
        parser.add_argument(
            "--name",
            help="name of migration",
        )

    def migrate(self, m):
        match m:
            case "merge_works":
                from catalog.common.migrations import merge_works_20250301

                merge_works_20250301()
            case "fix_deleted_edition":
                from catalog.common.migrations import fix_20250208

                fix_20250208()
            case "fix_bangumi":
                from catalog.common.migrations import fix_bangumi_20250420

                fix_bangumi_20250420()
            case _:
                self.stdout.write(self.style.ERROR("Unknown migration."))

    def external_search(self, q, cat):
        sites = SiteManager.get_sites_for_search()
        peers = FediverseInstance.get_peers_for_search()
        self.stdout.write(f"Searching {cat} '{q}' ...")
        self.stdout.write(f"Peers: {peers}")
        self.stdout.write(f"Sites: {sites}")
        start_time = time.time()
        results = ExternalSources.search(q, 1, cat)
        for r in results:
            self.stdout.write(f"{r}")
        self.stdout.write(
            self.style.SUCCESS(
                f"{time.time() - start_time} seconds, {len(results)} items."
            )
        )

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
        qs = Item.objects.all()
        total = qs.count()
        self.stdout.write("Checking duplicated/empty title/desc...")
        issues = 0
        urls = []
        for i in tqdm(qs.iterator(), total=total):
            changed = False
            for f in ["localized_title", "localized_description"]:
                o = getattr(i, f, [])
                n = []
                for x in o:
                    v = x.get("text")
                    if v and x not in n:
                        n.append({"lang": str(x.get("lang", "x")), "text": str(v)})
                if n != o:
                    changed = True
                    setattr(i, f, n)
            if changed:
                issues += 1
                if self.fix:
                    i.save()
            try:
                vv = i.ap_object
                if not vv:
                    continue
            except Exception:
                urls.append(i.absolute_url)
                self.stdout.write(f"! {i}")
        self.stdout.write(f"{issues} title issues found in {total} items.")
        self.stdout.write(f"{len(urls)} schema issues found in {total} items.")
        for i in urls:
            self.stdout.write(f"! {i}/edit")

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
                    i.recast_to(i.merged_to_item.__class__)

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

    def handle(
        self, action, query, yes, category, batch_size, url, name, *args, **options
    ):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        index = CatalogIndex.instance()
        match action:
            case "integrity":
                self.integrity()

            case "purge":
                self.purge()

            case "extsearch":
                self.external_search(query, category)

            case "idx-destroy":
                if yes or input(_CONFIRM).upper().startswith("Y"):
                    index.delete_collection()
                    self.stdout.write(self.style.SUCCESS("deleted."))

            case "idx-alt":
                # index.update_schema()
                self.stdout.write(self.style.SUCCESS("not implemented."))

            case "idx-init":
                index.initialize_collection()
                self.stdout.write(self.style.SUCCESS("initialized."))

            case "idx-info":
                try:
                    r = index.check()
                    self.stdout.write(str(r))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(str(e)))

            case "idx-delete":
                c = index.delete_all()
                self.stdout.write(self.style.SUCCESS(f"deleted {c} documents."))

            case "idx-reindex":
                items = Item.objects.filter(
                    is_deleted=False, merged_to_item_id__isnull=True
                ).order_by("id")
                c = 0
                t = 0
                pg = Paginator(items, batch_size)
                for p in tqdm(pg.page_range):
                    docs = index.items_to_docs(pg.get_page(p).object_list)
                    r = index.replace_docs(docs)
                    t += len(docs)
                    c += r
                self.stdout.write(self.style.SUCCESS(f"indexed {c} of {t} docs."))

            case "idx-get":
                item = Item.get_by_url(url)
                if not item:
                    self.stderr.write(self.style.ERROR("item not found."))
                else:
                    d = index.get_doc(item.pk)
                    self.stdout.write(json.dumps(d, indent=2))
                    d = item.to_indexable_doc()
                    self.stdout.write(json.dumps(d, indent=2))

            case "search":
                q = CatalogQueryParser("" if query == "-" else query, page_size=100)
                # if category:
                #     q.filter("category", category)
                r = index.search(q)
                self.stdout.write(self.style.SUCCESS(str(r)))
                self.stdout.write(f"{r.facet_by_item_class}")
                self.stdout.write(f"{r.facet_by_category}")
                for i in r:
                    self.stdout.write(str(i))

            case "migrate":
                if not name:
                    self.stdout.write(self.style.ERROR("name is required."))
                    return
                self.migrate(name)

            case _:
                self.stdout.write(self.style.ERROR("action not found."))

        self.stdout.write(self.style.SUCCESS("Done."))
