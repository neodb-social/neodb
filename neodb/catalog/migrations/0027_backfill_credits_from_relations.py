# Enqueue an async backfill of ItemCredit from the deprecated
# ItemPeopleRelation table (see catalog.common.migrations).

from django.db import migrations

from catalog.common.migrations import (
    backfill_credits_from_relations_20260719,
    enqueue_migration_job,
)


def queue_job(apps, schema_editor):
    enqueue_migration_job(backfill_credits_from_relations_20260719)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0026_unify_metadata"),
    ]

    operations = [
        migrations.RunPython(queue_job, migrations.RunPython.noop),
    ]
