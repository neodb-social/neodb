from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0017_alter_task_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="type",
            field=models.CharField(
                choices=[
                    ("journal.baseimporter", "base importer"),
                    ("journal.csvexporter", "csv exporter"),
                    ("journal.csvimporter", "csv importer"),
                    ("journal.doubakimporter", "doubak importer"),
                    ("journal.doubanimporter", "douban importer"),
                    ("journal.doufenexporter", "doufen exporter"),
                    ("journal.goodreadsimporter", "goodreads importer"),
                    ("journal.letterboxdimporter", "letterboxd importer"),
                    ("journal.ndjsonexporter", "ndjson exporter"),
                    ("journal.ndjsonimporter", "ndjson importer"),
                    ("journal.opmlimporter", "opml importer"),
                    ("journal.rymimporter", "rym importer"),
                    ("journal.steamimporter", "steam importer"),
                    ("journal.storygraphimporter", "story graph importer"),
                    ("journal.traktimporter", "trakt importer"),
                    ("journal.wordpressexporter", "wordpress exporter"),
                    ("journal.wordpressimporter", "wordpress importer"),
                ],
                db_index=True,
                max_length=255,
            ),
        ),
    ]
