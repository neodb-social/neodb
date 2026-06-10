from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("activities", "0029_post_url_db_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="timelineevent",
            index=models.Index(
                fields=["identity", "-id"],
                condition=models.Q(dismissed=False),
                name="te_identity_idneg_undismissed",
            ),
        ),
    ]
