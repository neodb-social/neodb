from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0022_alter_itemcredit_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ItemSimilarity",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("score", models.FloatField()),
                ("method", models.PositiveSmallIntegerField(default=0)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="similarity_out",
                        to="catalog.item",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="similarity_in",
                        to="catalog.item",
                    ),
                ),
            ],
            options={
                "db_table": "catalog_item_similarity",
            },
        ),
        migrations.AddConstraint(
            model_name="itemsimilarity",
            constraint=models.UniqueConstraint(
                fields=("source", "target", "method"),
                name="catalog_item_similarity_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="itemsimilarity",
            constraint=models.CheckConstraint(
                condition=~models.Q(source=models.F("target")),
                name="catalog_item_similarity_no_self",
            ),
        ),
        migrations.AddIndex(
            model_name="itemsimilarity",
            index=models.Index(
                fields=["source", "-score"],
                name="catalog_item_sim_src_score",
            ),
        ),
        migrations.CreateModel(
            name="UserRecommendation",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("score", models.FloatField()),
                ("seed_item_ids", models.JSONField(default=list)),
                ("category", models.CharField(db_index=True, max_length=20)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="recommended_to",
                        to="catalog.item",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="recommendations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "catalog_user_recommendation",
            },
        ),
        migrations.AddConstraint(
            model_name="userrecommendation",
            constraint=models.UniqueConstraint(
                fields=("user", "item"), name="catalog_user_reco_uniq"
            ),
        ),
        migrations.AddIndex(
            model_name="userrecommendation",
            index=models.Index(
                fields=["user", "category", "-score"],
                name="catalog_user_reco_lookup",
            ),
        ),
    ]
