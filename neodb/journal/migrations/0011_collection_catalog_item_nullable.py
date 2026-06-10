from django.db import migrations, models


class Migration(migrations.Migration):
    """Make ``Collection.catalog_item`` nullable.

    Remote (federated) Collection mirrors should not auto-create a
    ``CatalogCollection`` row — the catalog detail page belongs to the
    origin instance, not us. Allowing ``catalog_item`` to be NULL lets
    ``Collection.save`` skip the auto-create on ``local=False`` and
    avoids polluting the catalog with stub entries.

    The column type change from NOT NULL to NULL is metadata-only on
    Postgres and runs without a table rewrite.
    """

    dependencies = [
        ("journal", "0010_collectionmember_unique_collection_member"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collection",
            name="catalog_item",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="journal_item",
                to="catalog.collection",
            ),
        ),
    ]
