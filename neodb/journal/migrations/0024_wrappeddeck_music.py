from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("journal", "0023_wrappeddeck_audiences")]

    operations = [
        migrations.AddField(
            model_name="wrappeddeck",
            name="music",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
