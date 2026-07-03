from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("users", "0014_apidentity_local_username_ci_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="apidentity",
            index=models.Index(
                Upper("username"),
                Upper("domain_name"),
                condition=models.Q(deleted__isnull=True),
                name="ix_apidentity_handle_ci",
            ),
        ),
    ]
