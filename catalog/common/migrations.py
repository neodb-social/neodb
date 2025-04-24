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
