from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("activities", "0031_quoteauthorization"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="post",
            index=models.Index(fields=["author", "-id"], name="ix_post_author_id"),
        ),
    ]
