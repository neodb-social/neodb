from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("users", "0013_preference_auto_note_on_reply"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="apidentity",
            index=models.Index(
                Upper("username"),
                condition=models.Q(local=True, deleted__isnull=True),
                name="ix_apidentity_local_uname_ci",
            ),
        ),
    ]
