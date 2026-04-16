from datetime import timedelta

import django_rq
from django.conf import settings
from django.db import migrations

from catalog.common.migrations import populate_credits_extra_20260415


def queue_job(apps, schema_editor):
    skips = getattr(settings, "SKIP_MIGRATIONS", [])
    if "populate_credits_extra" in skips:
        print("(Skipped)", end="")
    else:
        django_rq.get_queue("cron").enqueue_in(
            timedelta(seconds=5), populate_credits_extra_20260415
        )
        print("(Queued)", end="")


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0017_normalize_lang"),
    ]

    operations = [
        migrations.RunPython(queue_job),
    ]
