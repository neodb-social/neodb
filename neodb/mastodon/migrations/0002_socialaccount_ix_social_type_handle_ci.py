from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("mastodon", "0001_initial_0_11"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="socialaccount",
            index=models.Index(
                models.F("type"),
                Upper("handle"),
                name="ix_social_type_handle_ci",
            ),
        ),
    ]
