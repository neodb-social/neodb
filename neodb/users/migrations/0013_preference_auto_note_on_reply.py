from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0012_task_rym_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="preference",
            name="auto_note_on_reply",
            field=models.BooleanField(default=True),
        ),
    ]
