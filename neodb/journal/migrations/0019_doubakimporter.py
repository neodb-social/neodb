from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0018_wordpressexporter_wordpressimporter"),
    ]

    operations = [
        migrations.CreateModel(
            name="DoubakImporter",
            fields=[],
            options={
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("journal.csvimporter",),
        ),
    ]
