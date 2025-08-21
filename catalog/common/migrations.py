from time import sleep

from django.db import connection, models
from loguru import logger
from tqdm import tqdm


def fix_20250208():
    logger.warning("Fixing soft-deleted editions...")
    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE catalog_item
            SET is_deleted = true
            WHERE id NOT IN ( SELECT item_ptr_id FROM catalog_edition ) AND polymorphic_ctype_id = (SELECT id FROM django_content_type WHERE app_label='catalog' AND model='edition');
            INSERT INTO catalog_edition (item_ptr_id)
            SELECT id FROM catalog_item
            WHERE id NOT IN ( SELECT item_ptr_id FROM catalog_edition ) AND polymorphic_ctype_id = (SELECT id FROM django_content_type WHERE app_label='catalog' AND model='edition');
        """)
    logger.warning("Fix complete.")


def merge_works_20250301():
    from catalog.models import Edition, Work

    logger.warning("Start merging works...")
    editions = Edition.objects.annotate(n=models.Count("works")).filter(n__gt=1)
    primary_work = []
    merge_map = {}
    for edition in tqdm(editions):
        w = Work.objects.filter(
            editions=edition, is_deleted=False, merged_to_item__isnull=True
        ).first()
        if w is None:
            logger.error(f"No active work found for {edition}")
            continue
        merge_to_id = w.pk
        if merge_to_id in merge_map:
            merge_to_id = merge_map[merge_to_id]
        elif merge_to_id not in primary_work:
            primary_work.append(merge_to_id)
        for work in Work.objects.filter(editions=edition).exclude(pk=w.pk):
            if work.pk in merge_map:
                if merge_map[work.pk] != merge_to_id:
                    logger.error(
                        f"{Work.objects.get(pk=merge_to_id)} and {Work.objects.get(pk=merge_map[work.pk])} might need to be merged manually."
                    )
            elif work.pk in primary_work:
                logger.error(
                    f"{Work.objects.get(pk=merge_to_id)} and {work} might need to be merged manually."
                )
            else:
                merge_map[work.pk] = merge_to_id

    logger.warning(
        f"{len(primary_work)} primay work total, and {len(merge_map)} merges will be processed."
    )
    for k, v in tqdm(merge_map.items()):
        from_work = Work.objects.get(pk=k)
        to_work = Work.objects.get(pk=v)
        # print(from_work, '->', to_work)
        from_work.merge_to(to_work)
        for edition in from_work.editions.all():
            # doing this as work.merge_to() may miss edition belonging to both from and to
            from_work.editions.remove(edition)
            to_work.editions.add(edition)

    logger.warning("Applying unique index...")
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS catalog_work_editions_work_id_uniq ON catalog_work_editions (edition_id);
            """)

    logger.warning("Merging works completed.")


def fix_bangumi_20250420():
    from catalog.models import Item

    logger.warning("Scaning catalog for issues.")
    fixed = 0
    for i in Item.objects.all().iterator():
        changed = False
        for a in ["location", "director", "language"]:
            v = getattr(i, a, None)
            if isinstance(v, str):
                setattr(i, a, v.split("、"))
                changed = True
        v = getattr(i, "pub_house", None)
        if isinstance(v, list):
            setattr(i, "pub_house", "/".join(v))
            changed = True
        if changed:
            i.save(update_fields=["metadata"])
            fixed += 1
    logger.warning(f"{fixed} items fixed.")


def reindex_20250424():
    from django.core.paginator import Paginator

    from catalog.index import CatalogIndex
    from catalog.models import Item

    logger.warning("Checking index status.")
    index = CatalogIndex.instance()
    s = index.initialize_collection(max_wait=30)
    if not s:
        logger.error("Index is not ready, reindexing aborted.")
        return
    logger.warning("Reindexing started.")
    items = Item.objects.filter(
        is_deleted=False, merged_to_item_id__isnull=True
    ).order_by("id")
    c = 0
    t = 0
    pg = Paginator(items, 1000)
    for p in tqdm(pg.page_range):
        docs = index.items_to_docs(pg.get_page(p).object_list)
        r = index.replace_docs(docs)
        t += len(docs)
        c += r
    logger.warning(f"Reindexing complete: updated {c} of {t} docs.")


def normalize_language_20250524():
    from catalog.models import Item
    from common.models.lang import normalize_languages

    logger.warning("normalize_language start")
    c = Item.objects.all().count()
    u = 0
    for i in tqdm(Item.objects.all().iterator(), total=c):
        lang = getattr(i, "language", None)
        if lang:
            lang2 = normalize_languages(lang)
            if lang2 != lang:
                setattr(i, "language", lang2)
                i.save(update_fields=["metadata"])
                u += 1
    logger.warning(f"normalize_language finished. {u} of {c} items updated.")


def link_tmdb_wikidata_20250815(limit=None):
    """
    Scan all TMDB Movie and TVShow resources, refetch them, and link to WikiData resources if available.

    This function:
    1. Finds all ExternalResources with TMDB Movie and TVShow ID types
    2. Refetches each TMDB resource to ensure we have the latest data
    3. If the TMDB resource has a WikiData ID, fetches the corresponding WikiData resource
    4. Links both resources to the same Item
    """
    from catalog.common import IdType, SiteManager
    from catalog.common.models import ExternalResource
    from catalog.sites.wikidata import WikiData

    logger.warning("Starting TMDB-WikiData linking process")
    tmdb_resources = ExternalResource.objects.filter(
        id_type__in=[IdType.TMDB_Movie, IdType.TMDB_TV]
    )
    if limit:
        tmdb_resources = tmdb_resources[:limit]
    count_total = tmdb_resources.count()
    count_with_wikidata = 0
    count_errors = 0
    logger.warning(f"Found {count_total} TMDB resources to process")
    for resource in tqdm(tmdb_resources, total=count_total):
        try:
            site_cls = SiteManager.get_site_cls_by_id_type(resource.id_type)
            if not site_cls:
                logger.error(f"Could not find site class for {resource.id_type}")
                count_errors += 1
                continue
            site = site_cls(resource.url)
            try:
                resource_content = site.scrape()
            except Exception as e:
                logger.error(f"Failed to scrape {resource.url}: {e}")
                count_errors += 1
                continue
            wikidata_id = resource_content.lookup_ids.get(IdType.WikiData)
            if not wikidata_id:
                continue
            resource.update_content(resource_content)
            count_with_wikidata += 1
            wiki_site = WikiData(id_value=wikidata_id)
            try:
                wiki_site.get_resource_ready()
                logger.success(f"Linked WikiData {wiki_site} to {site}")
            except Exception as e:
                logger.error(
                    f"Failed to process WikiData {e}", extra={"qid": wikidata_id}
                )
                count_errors += 1
            sleep(0.5)
        except Exception as e:
            logger.error(f"Error processing resource {resource}: {e}")
            count_errors += 1
    logger.warning("TMDB-WikiData linking process completed:")
    logger.warning(f"  Total TMDB resources processed: {count_total}")
    logger.warning(f"  TMDB resources with WikiData IDs: {count_with_wikidata}")
    logger.warning(f"  Errors encountered: {count_errors}")
    return {
        "total": count_total,
        "with_wikidata": count_with_wikidata,
        "errors": count_errors,
    }
