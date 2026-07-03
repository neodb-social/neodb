from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("users", "0032_add_account_note"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="identity",
            index=models.Index(
                Upper("username"),
                models.F("domain"),
                condition=models.Q(local=True),
                name="ix_identity_local_uname_ci",
            ),
        ),
    ]
