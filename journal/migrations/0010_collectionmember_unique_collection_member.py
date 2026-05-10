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
    delete the rest. Use the historical ORM (``apps.get_model``) so the
    multi-table-inheritance PK (``piece_ptr_id``, not ``id``) and the
    parent ``Piece`` row are handled correctly across PG / SQLite.
    """
    CollectionMember = apps.get_model("journal", "CollectionMember")
    db_alias = schema_editor.connection.alias
    seen: set[tuple[int, int]] = set()
    duplicate_pks: list[int] = []
    qs = (
        CollectionMember.objects.using(db_alias)
        .order_by("parent_id", "item_id", "pk")
        .values_list("pk", "parent_id", "item_id")
    )
    for pk, parent_id, item_id in qs.iterator():
        key = (parent_id, item_id)
        if key in seen:
            duplicate_pks.append(pk)
        else:
            seen.add(key)
    if duplicate_pks:
        # Bulk-delete the duplicate child rows. ``Piece`` parent rows
        # are cascaded via the PolymorphicModel multi-table-inheritance
        # FK, so this leaves no orphan piece rows.
        for chunk_start in range(0, len(duplicate_pks), 1000):
            chunk = duplicate_pks[chunk_start : chunk_start + 1000]
            CollectionMember.objects.using(db_alias).filter(pk__in=chunk).delete()


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
