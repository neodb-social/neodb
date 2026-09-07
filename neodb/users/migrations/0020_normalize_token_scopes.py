# Enqueue an async rewrite of takahe Token.scopes from the legacy string
# form into the list form (see users.jobs.migrations).

from django.db import migrations

from catalog.common.migrations import enqueue_migration_job


def queue_job(apps: object, schema_editor: object) -> None:
    enqueue_migration_job("users.jobs.migrations:normalize_token_scopes_20260907")


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0019_webhook"),
    ]

    operations = [
        migrations.RunPython(queue_job, migrations.RunPython.noop),
    ]
