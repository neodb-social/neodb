from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0010_preference_disabled_search_sources"),
    ]

    operations = [
        migrations.AddField(
            model_name="preference",
            name="disable_recommendations",
            field=models.BooleanField(default=False),
        ),
    ]
