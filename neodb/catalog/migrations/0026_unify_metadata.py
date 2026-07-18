from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_unify_metadata(apps, schema_editor):
    enqueue_migration_job("unify_metadata_20260715")


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0025_podcastepisode_covering_index"),
    ]

    operations = [
        migrations.RunPython(queue_unify_metadata),
    ]
