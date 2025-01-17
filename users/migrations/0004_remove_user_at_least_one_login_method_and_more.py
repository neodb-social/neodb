# Generated by Django 4.2.13 on 2024-07-02 18:06

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_remove_preference_no_anonymous_view"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="at_least_one_login_method",
        ),
        migrations.RemoveConstraint(
            model_name="user",
            name="unique_email",
        ),
        migrations.RemoveConstraint(
            model_name="user",
            name="unique_mastodon_username",
        ),
        migrations.RemoveConstraint(
            model_name="user",
            name="unique_mastodon_id",
        ),
        migrations.RemoveIndex(
            model_name="user",
            name="users_user_mastodo_bd2db5_idx",
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(models.F("is_active"), name="index_user_is_active"),
        ),
    ]
