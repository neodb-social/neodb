from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0024_post_application"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="quote_url",
            field=models.CharField(
                blank=True, db_index=True, max_length=2048, null=True
            ),
        ),
    ]
