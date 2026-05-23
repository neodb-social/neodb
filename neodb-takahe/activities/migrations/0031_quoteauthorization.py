import core.snowflake
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0030_timelineevent_undismissed_idx"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuoteAuthorization",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=core.snowflake.Snowflake.generate_post,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("interacting_object_uri", models.CharField(max_length=2048)),
                (
                    "request_uri",
                    models.CharField(blank=True, max_length=2048, null=True),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "target_post",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="quote_authorizations",
                        to="activities.post",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["target_post", "interacting_object_uri"],
                        name="activities__target__448802_idx",
                    ),
                ],
            },
        ),
    ]
