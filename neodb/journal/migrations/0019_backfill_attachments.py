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

    Deployments old enough to hold images from before the
    ``upload/<identity_id>/`` convention should run ``neodb-manage
    migrate_images`` first and then re-run this job: such an image cannot be
    attributed to an owner safely, so it is skipped and would otherwise stay
    outside the registry. The job logs a count of those. Note that
    ``migrate_images`` reads Review and Collection only, so an Article body
    with pre-convention paths needs moving by hand.

    A separate count covers images left unlinked for belonging to another user
    (a hotlink); those are expected and need nothing done.
    """

    dependencies = [
        ("journal", "0018_attachment"),
    ]

    operations = [
        migrations.RunPython(queue_backfill, migrations.RunPython.noop),
    ]
