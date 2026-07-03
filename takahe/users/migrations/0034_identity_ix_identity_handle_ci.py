from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("users", "0033_identity_local_username_ci_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="identity",
            index=models.Index(
                Upper("username"),
                Upper("domain"),
                name="ix_identity_handle_ci",
            ),
        ),
    ]
