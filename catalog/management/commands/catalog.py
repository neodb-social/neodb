import json
import logging
import sys
import time
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db.models import Count, F
from django.utils import timezone
from loguru import logger
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
wikidata-tmdb:  link TMDB resources to WikiData resources (using TMDB API)
wikidata-link:  link external resources to WikiData (use --query for IdType)
wikidata-find:  lookup Wikidata QID from URL and scrape (use --query for URL)
idx-info:       show index information
idx-init:       check and create index if not exists
idx-destroy:    delete index
idx-alt:        update index schema
idx-delete:     delete docs in index
idx-reindex:    reindex docs
idx-get:        dump one doc (use --query for URL)
idx-catchup:    update index for items edited in last X hours (use --hour)
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
                "wikidata-tmdb",
                "wikidata-link",
                "wikidata-find",
                "idx-info",
                "idx-init",
                "idx-alt",
                "idx-destroy",
                "idx-reindex",
                "idx-delete",
                "idx-get",
                "idx-catchup",
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
            "-q",
            help="Search query",
        )
        parser.add_argument(
            "--batch-size",
            default=1000,
        )
        parser.add_argument(
            "--name",
            help="name of migration",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="limit number of items to process",
        )
        parser.add_argument(
            "--start",
            type=int,
            help="starting pk for processing",
        )
        parser.add_argument(
            "--log-level",
            choices=[
                "TRACE",
                "DEBUG",
                "INFO",
                "SUCCESS",
                "WARNING",
                "ERROR",
                "CRITICAL",
            ],
            default="INFO",
            help="Set logging level (default: INFO)",
        )
        parser.add_argument(
            "--hour",
            type=int,
            help="Number of hours to look back for edited items (used with idx-catchup)",
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
            case "normalize_language":
                from catalog.common.migrations import normalize_language_20250524

                normalize_language_20250524()
            case "link_tmdb_wikidata":
                from catalog.common.migrations import link_tmdb_wikidata_20250815

                link_tmdb_wikidata_20250815()
            case "fix_missing_cover":
                from catalog.common.migrations import fix_missing_cover_20250821

                fix_missing_cover_20250821()
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

    def link_wikidata(self, limit=None):
        """Link TMDB resources to WikiData resources"""
        from catalog.common.migrations import link_tmdb_wikidata_20250815

        self.stdout.write("Starting TMDB-WikiData linking process...")
        start_time = time.time()

        # Convert limit to int if provided
        if limit and limit.isdigit():
            limit = int(limit)
        else:
            limit = None

        # Run the linking process
        results = link_tmdb_wikidata_20250815(limit)

        # Output results
        self.stdout.write(
            self.style.SUCCESS("TMDB-WikiData linking process completed:")
        )
        self.stdout.write(f"  Total TMDB resources processed: {results['total']}")
        self.stdout.write(
            f"  TMDB resources with WikiData IDs: {results['with_wikidata']}"
        )
        self.stdout.write(f"  Errors encountered: {results['errors']}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Process completed in {time.time() - start_time:.2f} seconds."
            )
        )

    def wikidata_link(self, id_type_str, limit=None, start_pk=None):
        """Link external resources to WikiData resources using lookup_qid_by_external_id"""
        from catalog.common import IdType
        from catalog.common.models import ExternalResource
        from catalog.sites.wikidata import WikiData

        types = [t.value for t in IdType]
        if not id_type_str:
            self.stdout.write(
                self.style.ERROR("IdType is required. Use --query <IdType>")
            )
            self.stdout.write(f"Available IdTypes: {', '.join(types)}")
            return
        try:
            id_type = IdType(id_type_str.lower())
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid IdType: {id_type_str}"))
            self.stdout.write(f"Available IdTypes: {', '.join(types)}")
            return

        self.stdout.write(f"Starting WikiData linking for {id_type.label}...")
        if start_pk:
            self.stdout.write(f"Starting from pk >= {start_pk}")
        start_time = time.time()

        qs = (
            ExternalResource.objects.filter(id_type=id_type, item__isnull=False)
            .exclude(other_lookup_ids__has_key=IdType.WikiData.value)
            .select_related("item")
            .order_by("pk")
        )
        if start_pk:
            qs = qs.filter(pk__gte=start_pk)
        if limit:
            qs = qs[:limit]
        total = qs.count()
        linked = 0
        errors = 0

        with tqdm(total=min(total, limit or total)) as pbar:
            for resource in qs.iterator():
                pbar.set_description(f"id:{resource.pk}")
                qid = WikiData.lookup_qid_by_external_id(id_type, resource.id_value)
                pbar.update(1)
                time.sleep(0.5)
                site = SiteManager.get_site_by_id(IdType.WikiData, qid) if qid else None
                try:
                    if site and site.get_resource_ready():
                        linked += 1
                except Exception as e:
                    errors += 1
                    s = f"Error processing res:{resource.pk} {resource.url}: {e}"
                    self.stdout.write(self.style.ERROR(s))

        self.stdout.write(self.style.SUCCESS("\nWikiData linking completed:"))
        self.stdout.write(f"  Total {id_type.label} resources processed: {total}")
        self.stdout.write(f"  Successfully linked to WikiData: {linked}")
        self.stdout.write(f"  Errors encountered: {errors}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Process completed in {time.time() - start_time:.2f} seconds."
            )
        )

    def wikidata_lookup(self, url):
        """Lookup Wikidata QID from external site URL and scrape"""
        from catalog.sites.wikidata import WikiData

        if not url:
            self.stdout.write(self.style.ERROR("URL is required. Use --query <URL>"))
            return

        self.stdout.write(f"Processing URL: {url}")

        # Try to get site and extract ID
        try:
            site = SiteManager.get_site_by_url(url)
            if not site:
                self.stdout.write(
                    self.style.ERROR(f"Could not identify site from URL: {url}")
                )
                return

            id_type = site.ID_TYPE
            id_value = site.id_value

            self.stdout.write(f"Detected: {site.SITE_NAME} / {id_type} / {id_value}")

            if not id_value or not id_type:
                self.stdout.write(
                    self.style.ERROR(f"Could not extract ID from URL: {url}")
                )
                return
            # Lookup QID
            qid = WikiData.lookup_qid_by_external_id(id_type, id_value)

            if not qid:
                self.stdout.write(
                    self.style.WARNING(
                        f"No Wikidata QID found for {id_type}:{id_value}"
                    )
                )
                return

            self.stdout.write(self.style.SUCCESS(f"Found Wikidata QID: {qid}"))

            # Scrape Wikidata
            wikidata_url = f"https://www.wikidata.org/wiki/{qid}"
            self.stdout.write(f"Wikidata URL: {wikidata_url}")

            wd_site = SiteManager.get_site_by_url(wikidata_url)
            if wd_site:
                self.stdout.write("\nScraping Wikidata entity...")
                result = wd_site.scrape()

                # Display results
                self.stdout.write(self.style.SUCCESS("\n=== Scrape Results ==="))

                if result.metadata:
                    self.stdout.write("\nMetadata:")
                    for key, value in result.metadata.items():
                        if key not in ["localized_title", "localized_description"]:
                            self.stdout.write(f"  {key}: {value}")

                    # Display localized titles
                    if "localized_title" in result.metadata:
                        self.stdout.write("\nLocalized Titles:")
                        for item in result.metadata["localized_title"]:
                            self.stdout.write(f"  [{item['lang']}] {item['text']}")

                    # Display localized descriptions
                    if "localized_description" in result.metadata:
                        self.stdout.write("\nLocalized Descriptions:")
                        for item in result.metadata["localized_description"]:
                            self.stdout.write(f"  [{item['lang']}] {item['text']}")

                if result.lookup_ids:
                    self.stdout.write("\nExternal IDs found:")
                    for id_type, id_val in result.lookup_ids.items():
                        self.stdout.write(f"  {id_type}: {id_val}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def idx_catchup(self, hours):
        """Update index for items edited in the last X hours"""
        if hours is None:
            self.stdout.write(self.style.ERROR("--hour parameter is required"))
            return
        cutoff_time = timezone.now() - timedelta(hours=hours)
        items = Item.objects.filter(
            edited_time__gte=cutoff_time, is_deleted=False, merged_to_item__isnull=True
        ).order_by("pk")
        total_count = items.count()
        self.stdout.write(f"Found {total_count} items edited since: {cutoff_time}")
        updated_count = 0
        with tqdm(total=total_count, desc="Updating index") as pbar:
            for item in items.iterator():
                try:
                    item.update_index()
                    updated_count += 1
                    pbar.set_description(f"Updated: {item.title[:30]}...")
                except Exception as e:
                    logger.error(f"Error updating index for item {item.pk}: {e}")
                pbar.update(1)

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
                if self.fix and i.merged_to_item:
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
        self,
        action,
        query,
        yes,
        category,
        batch_size,
        name,
        limit,
        start,
        *args,
        **options,
    ):
        self.verbose = options["verbose"]
        self.fix = options["fix"]

        log_level = options.get("log_level", "INFO")
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logging.getLogger("rq").setLevel(logging.WARNING)

        index = CatalogIndex.instance()
        match action:
            case "integrity":
                self.integrity()

            case "purge":
                self.purge()

            case "extsearch":
                self.external_search(query, category)

            case "wikidata-tmdb":
                self.link_wikidata()

            case "wikidata-link":
                self.wikidata_link(query, limit, start)

            case "wikidata-find":
                self.wikidata_lookup(query)

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
                item = Item.get_by_url(query)
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

            case "idx-catchup":
                hour = options.get("hour")
                self.idx_catchup(hour)

            case _:
                self.stdout.write(self.style.ERROR("action not found."))

        self.stdout.write(self.style.SUCCESS("Done."))
