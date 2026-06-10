from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0021_reindex_people"),
    ]

    operations = [
        migrations.AlterField(
            model_name="itemcredit",
            name="role",
            field=models.CharField(
                choices=[
                    ("author", "author"),
                    ("translator", "translator"),
                    ("director", "director"),
                    ("playwright", "playwright"),
                    ("actor", "actor"),
                    ("producer", "producer"),
                    ("artist", "artist"),
                    ("designer", "designer"),
                    ("composer", "composer"),
                    ("choreographer", "choreographer"),
                    ("performer", "performer"),
                    ("host", "host"),
                    ("original_creator", "original creator"),
                    ("crew", "crew"),
                    ("publisher", "publisher"),
                    ("developer", "developer"),
                    ("production_company", "production company"),
                    ("record_label", "record label"),
                    ("distributor", "distributor"),
                    ("studio", "studio"),
                    ("troupe", "troupe"),
                ],
                max_length=100,
                verbose_name="role",
            ),
        ),
    ]
