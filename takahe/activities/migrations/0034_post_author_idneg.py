from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("activities", "0033_conversation_uri"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="post",
            index=models.Index(fields=["author", "-id"], name="post_author_idneg"),
        ),
    ]
