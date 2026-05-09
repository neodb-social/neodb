from django.db import migrations, models


def _dedupe_collection_members(apps, schema_editor):
    """Remove duplicate ``(parent, item)`` ``CollectionMember`` rows so the
    new ``unique_collection_member`` constraint can be added.

    Existing NeoDB deployments may have duplicate rows from past data
    paths that pre-dated the per-list dedup logic — most commonly when
    two catalog items were merged together and both ended up on the
    same collection. ``Collection.append_item`` short-circuits on the
    happy path, but the merge path bypassed it. Without this dedup the
    ``AddConstraint`` step below would abort the migration on those
    deployments.

    Strategy: keep the lowest-pk row per ``(parent_id, item_id)`` and
    delete the rest via raw SQL — fast and avoids loading every member
    through the ORM, which can be heavy on large collections.
    """
    CollectionMember = apps.get_model("journal", "CollectionMember")
    db_alias = schema_editor.connection.alias
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM journal_collectionmember
            WHERE id IN (
                SELECT id FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY parent_id, item_id
                            ORDER BY id
                        ) AS rn
                    FROM journal_collectionmember
                ) ranked
                WHERE rn > 1
            )
            """
        )
    # Touch CollectionMember to silence "unused import" warnings in
    # test discovery; also lets us assert the dedup ran in the unit
    # tests via ``apps.get_model`` reflection.
    _ = CollectionMember.objects.using(db_alias).count()


class Migration(migrations.Migration):
    """Enforce ``UniqueConstraint(parent, item)`` on ``CollectionMember``.

    Two concurrent member-sync jobs (inbound update + URL paste + retries)
    could race past the ``select_for_update`` guard on ``Collection`` and
    both insert a row for the same ``(parent, item)`` pair. The constraint
    is the belt under that suspender; ``select_for_update`` is still
    enforced at the ORM level because constraint violations fail the
    transaction rather than silently dedup.

    Pre-existing data may not satisfy this invariant if a deployment
    has previously merged catalog items that were both members of the
    same collection. ``_dedupe_collection_members`` runs first to keep
    the lowest-pk row per ``(parent, item)`` so ``AddConstraint`` can
    succeed; ``RunPython.noop`` is the inverse because the constraint
    add itself is what re-locks the data.
    """

    dependencies = [
        ("journal", "0009_remote_id_async"),
    ]

    operations = [
        migrations.RunPython(
            _dedupe_collection_members, reverse_code=migrations.RunPython.noop
        ),
        migrations.AddConstraint(
            model_name="collectionmember",
            constraint=models.UniqueConstraint(
                fields=("parent", "item"), name="unique_collection_member"
            ),
        ),
    ]
