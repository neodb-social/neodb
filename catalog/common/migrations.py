from typing import TYPE_CHECKING

from django.db import connection, transaction
from loguru import logger

if TYPE_CHECKING:
    from catalog.models import Item


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


def _merge_to(self, to_item: "Item | None"):
    if to_item is None:
        if self.merged_to_item is not None:
            self.merged_to_item = None
            self.save()
        return
    if to_item.pk == self.pk:
        return
    if to_item.merged_to_item is not None:
        return
    if not isinstance(to_item, self.__class__):
        raise ValueError("cannot merge to item in a different model")
    self.merged_to_item = to_item
    self.save()
    for res in self.external_resources.all():
        res.item = to_item
        res.save()
    for edition in self.editions.all():
        edition.related_work = to_item
        edition.save()
    to_item.save()


def merge_works_20250301(Edition):
    # Work = apps.get_model("catalog", "Work")
    with transaction.atomic():
        for edition in Edition.objects.all():
            works = edition.works.all()
            if not works.exists():
                continue
            if edition.related_work is None:
                related_work = works.first()
                while related_work.merged_to_item is not None:
                    related_work = related_work.merged_to_item
                try:
                    edition.related_work = related_work
                    edition.save()
                except Exception as e:
                    # do not know why, some work's class will be Item and cause error
                    logger.warning(
                        f"Error setting related_work for {edition} to {related_work}: {e}"
                    )
                    continue
            for work in works:
                if work.pk == edition.related_work.pk:
                    continue
                while work.merged_to_item is not None:
                    work = work.merged_to_item
                _merge_to(work, edition.related_work)
