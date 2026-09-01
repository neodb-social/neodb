import django.utils.timezone
from django.db import migrations, models

# Django migration classes intentionally use mutable class-level operation lists.
# ruff: noqa: RUF012


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0003_default_user_icon_png"),
    ]

    operations = [
        migrations.CreateModel(
            name="DurableDispatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("responsibility_ref", models.CharField(max_length=255)),
                (
                    "queue",
                    models.CharField(
                        choices=[
                            ("mastodon", "Mastodon"),
                            ("export", "Export"),
                            ("import", "Import"),
                            ("fetch", "Fetch"),
                            ("crawl", "Crawl"),
                            ("ap", "ActivityPub"),
                            ("cron", "Cron"),
                        ],
                        default="cron",
                        max_length=16,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("ready", "Ready"),
                            ("claimed", "Claimed"),
                            ("observation", "Needs observation"),
                            ("retired", "Retired"),
                        ],
                        default="ready",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=5)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                (
                    "next_attempt_at",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        null=True,
                    ),
                ),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "lease_token",
                    models.CharField(blank=True, max_length=32, null=True, unique=True),
                ),
                (
                    "last_outcome",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("known_success", "Known success"),
                            ("owner_rejected", "Owner rejected"),
                            ("safe_retry", "Safe retry"),
                            ("ambiguous", "Ambiguous"),
                            ("lease_expired", "Lease expired"),
                            ("enqueue_error", "Enqueue error"),
                        ],
                        default="",
                        max_length=20,
                    ),
                ),
                (
                    "last_error_category",
                    models.CharField(blank=True, default="", max_length=40),
                ),
                (
                    "last_error_text",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("last_error_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["next_attempt_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["state", "next_attempt_at"],
                        name="durdispatch_ready_idx",
                    ),
                    models.Index(
                        fields=["state", "lease_expires_at"],
                        name="durdispatch_lease_idx",
                    ),
                    models.Index(
                        fields=["responsibility_ref"],
                        name="durdispatch_ref_idx",
                    ),
                ],
            },
        ),
    ]
