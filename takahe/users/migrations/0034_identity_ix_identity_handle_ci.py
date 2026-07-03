from django.contrib.postgres.operations import (
    AddIndexConcurrently,
    RemoveIndexConcurrently,
)
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
                models.F("domain"),
                name="ix_identity_handle_ci",
            ),
        ),
        # Redundant once the index above exists: it serves the local lookup
        # with both columns as index conditions, local checked on the row.
        RemoveIndexConcurrently(
            model_name="identity",
            name="ix_identity_local_uname_ci",
        ),
    ]
