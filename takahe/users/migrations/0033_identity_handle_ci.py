from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("users", "0032_add_account_note"),
    ]

    operations = [
        # No-op on fresh databases; cleans up after the interim
        # 0033_identity_local_username_ci_index migration that briefly
        # existed on main (merged but never released).
        migrations.RunSQL(
            sql="DROP INDEX CONCURRENTLY IF EXISTS ix_identity_local_uname_ci",
            reverse_sql=migrations.RunSQL.noop,
        ),
        AddIndexConcurrently(
            model_name="identity",
            index=models.Index(
                Upper("username"),
                models.F("domain"),
                name="ix_identity_handle_ci",
            ),
        ),
    ]
