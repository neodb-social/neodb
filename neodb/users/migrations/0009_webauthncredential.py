import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_preference_mastodon_boost_enabled"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebAuthnCredential",
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
                (
                    "name",
                    models.CharField(
                        default="Passkey", max_length=255, verbose_name="name"
                    ),
                ),
                (
                    "credential_id",
                    models.BinaryField(unique=True, verbose_name="credential ID"),
                ),
                (
                    "public_key",
                    models.BinaryField(verbose_name="public key"),
                ),
                (
                    "sign_count",
                    models.PositiveIntegerField(default=0, verbose_name="sign count"),
                ),
                (
                    "transports",
                    models.JSONField(default=list, verbose_name="transports"),
                ),
                (
                    "created",
                    models.DateTimeField(auto_now_add=True, verbose_name="created"),
                ),
                (
                    "last_used",
                    models.DateTimeField(
                        default=None, null=True, verbose_name="last used"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="webauthn_credentials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user"], name="index_webauthn_user"),
                ],
            },
        ),
    ]
