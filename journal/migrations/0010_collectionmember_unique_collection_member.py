from django.db import migrations, models


class Migration(migrations.Migration):
    """Enforce ``UniqueConstraint(parent, item)`` on ``CollectionMember``.

    Two concurrent member-sync jobs (inbound update + URL paste + retries)
    could race past the ``select_for_update`` guard on ``Collection`` and
    both insert a row for the same ``(parent, item)`` pair. The constraint
    is the belt under that suspender; ``select_for_update`` is still
    enforced at the ORM level because constraint violations fail the
    transaction rather than silently dedup.

    Existing data already satisfies this invariant — ``Collection.append_item``
    short-circuits when the item is already a member — so the constraint
    can be added without a data migration.
    """

    dependencies = [
        ("journal", "0009_remote_id_async"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="collectionmember",
            constraint=models.UniqueConstraint(
                fields=("parent", "item"), name="unique_collection_member"
            ),
        ),
    ]
