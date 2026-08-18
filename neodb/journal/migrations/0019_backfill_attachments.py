from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_backfill(apps: object, schema_editor: object) -> None:
    enqueue_migration_job("journal.jobs.migrations:backfill_attachments_20260818")


class Migration(migrations.Migration):
    """Register pre-existing user uploads in the attachment registry.

    Runs in the background: Article / Review / Collection bodies only need
    their existing files adopted in place, but Note media has to be copied out
    of takahe's storage one object at a time, which is far too slow to hold a
    deploy open.

    Notes keep their legacy ``attachments`` JSON. ``Note.attachment_list``
    prefers the new rows and falls back to the JSON, so cards keep rendering
    while the job works through them; the column can be dropped in a
    follow-up once deployments have completed the backfill.
    """

    dependencies = [
        ("journal", "0018_attachment"),
    ]

    operations = [
        migrations.RunPython(queue_backfill, migrations.RunPython.noop),
    ]
