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


def merge_works_20250301(Edition, Work):
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

    logger.warning("Applying unique index...")
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS catalog_work_editions_work_id_uniq ON catalog_work_editions (edition_id);
            """)

    logger.warning("Merging works completed.")
