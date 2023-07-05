# Generated by Django 3.2.19 on 2023-06-30 02:39

import django.contrib.auth.validators
from django.db import migrations, models
import users.models
from django.conf import settings


def move_username(apps, schema_editor):
    User = apps.get_model("users", "User")
    for u in User.objects.all():
        u.mastodon_username = u.username
        u.username = u.mastodon_username + "@" + u.mastodon_site
        u.save()


def clear_username(apps, schema_editor):
    User = apps.get_model("users", "User")
    for u in User.objects.all():
        u.username = None if settings.ALLOW_ANY_SITE else u.mastodon_username
        u.save()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_alter_preference_classic_homepage"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="unique_user_identity",
        ),
        migrations.AddField(
            model_name="user",
            name="mastodon_username",
            field=models.CharField(default=None, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="mastodon_site",
            field=models.CharField(default=None, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="mastodon_id",
            field=models.CharField(default=None, max_length=100, null=True),
        ),
        migrations.RunPython(move_username),
        migrations.RunSQL(
            "UPDATE users_user SET mastodon_id = null where mastodon_id = '0';"
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                error_messages={"unique": "A user with that username already exists."},
                help_text="Required. 50 characters or fewer. Letters, digits and _ only.",
                max_length=100,
                null=True,
                unique=True,
                validators=[users.models.UsernameValidator()],
                verbose_name="username",
            ),
        ),
        migrations.RunPython(clear_username),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("mastodon_username", "mastodon_site"),
                name="unique_mastodon_username",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("mastodon_id", "mastodon_site"), name="unique_mastodon_id"
            ),
        ),
    ]
