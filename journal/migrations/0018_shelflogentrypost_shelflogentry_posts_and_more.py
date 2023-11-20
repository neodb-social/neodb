# Generated by Django 4.2.7 on 2023-11-20 06:52

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("takahe", "0001_initial"),
        ("journal", "0017_alter_piece_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShelfLogEntryPost",
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
                    "log_entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="journal.shelflogentry",
                    ),
                ),
                (
                    "post",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="takahe.post",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="shelflogentry",
            name="posts",
            field=models.ManyToManyField(
                related_name="log_entries",
                through="journal.ShelfLogEntryPost",
                to="takahe.post",
            ),
        ),
        migrations.AddConstraint(
            model_name="shelflogentrypost",
            constraint=models.UniqueConstraint(
                fields=("log_entry", "post"), name="unique_log_entry_post"
            ),
        ),
    ]
