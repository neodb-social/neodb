from django.db import migrations

from catalog.common.migrations import (
    edition_publisher_imprint_to_list_20260428,
    enqueue_migration_job,
)


def queue_job(apps, schema_editor):
    enqueue_migration_job(edition_publisher_imprint_to_list_20260428)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0022_alter_itemcredit_role"),
    ]

    operations = [
        migrations.RunPython(queue_job, reverse_code=migrations.RunPython.noop),
    ]
