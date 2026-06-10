from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("activities", "0028_previewcard_post_preview_card"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="post",
            index=models.Index(fields=["url"], name="activities_post_url_idx"),
        ),
    ]
