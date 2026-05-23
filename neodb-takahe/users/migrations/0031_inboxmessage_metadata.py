from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0030_alter_identity_icon_uri_alter_identity_image_uri"),
    ]

    operations = [
        migrations.AddField(
            model_name="inboxmessage",
            name="metadata",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
