import catalog.models.utils
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0016_backfill_member_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="cover",
            field=models.ImageField(
                blank=True,
                default="item/default.svg",
                upload_to=catalog.models.utils.piece_cover_path,
            ),
        ),
    ]
