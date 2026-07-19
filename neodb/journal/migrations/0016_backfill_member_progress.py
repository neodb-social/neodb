from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_backfill(apps: object, schema_editor: object) -> None:
    enqueue_migration_job(
        "journal.jobs.migrations:backfill_member_progress_from_notes_20260720"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0015_shelfmemberprogress"),
    ]

    operations = [
        migrations.RunPython(queue_backfill, migrations.RunPython.noop),
    ]
