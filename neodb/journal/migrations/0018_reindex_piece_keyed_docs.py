from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_reindex(apps: object, schema_editor: object) -> None:
    enqueue_migration_job("journal.jobs.migrations:reindex_piece_keyed_docs_20260818")


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0017_article_cover"),
    ]

    operations = [
        migrations.RunPython(queue_reindex, migrations.RunPython.noop),
    ]
