from django.db import migrations

from journal.jobs.migrations import backfill_attachments_20260818


def backfill(apps: object, schema_editor: object) -> None:
    # The real models, not the historical ones: the backfill copies Note
    # media out of takahe's storage through Attachment.sync_from_post.
    backfill_attachments_20260818()


class Migration(migrations.Migration):
    """Register pre-existing user uploads in the attachment registry.

    Article / Review / Collection bodies have their existing files adopted in
    place; Note media is copied out of takahe's storage into our own. Runs
    synchronously: from this release ``Note.attachment_list`` reads the
    registry only and nothing writes the legacy ``attachments`` JSON, so a
    note left out of the registry would lose its media from the web, the API
    and exports. Each piece is handled in its own savepoint; body linking is
    idempotent and notes that already hold rows are skipped, so re-running
    the backfill is safe.

    The legacy column is deprecated and stays, with this backfill as its only
    reader, until a later release drops it.

    Deployments old enough to hold images from before the
    ``upload/<identity_id>/`` convention should run ``neodb-manage
    migrate_images`` first and then re-run ``backfill_attachments_20260818``:
    such an image cannot be attributed to an owner safely, so it is skipped
    and would otherwise stay outside the registry. The backfill logs a count
    of those. Note that ``migrate_images`` reads Review and Collection only,
    so an Article body with pre-convention paths needs moving by hand.

    A separate count covers images left unlinked for belonging to another user
    (a hotlink); those are expected and need nothing done.
    """

    dependencies = [
        ("journal", "0018_attachment"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
