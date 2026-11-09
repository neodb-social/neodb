# Enqueue a one-off registration of the BookWyrm servers FediDB lists
# (see journal.jobs.migrations).

from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_job(apps: object, schema_editor: object) -> None:
    enqueue_migration_job("journal.jobs.migrations:register_bookwyrm_peers_20261109")


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0020_hardcoverimporter"),
    ]

    operations = [
        migrations.RunPython(queue_job, migrations.RunPython.noop),
    ]
