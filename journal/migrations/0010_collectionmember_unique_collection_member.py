from django.db import migrations, models


def _dedupe_collection_members(apps, schema_editor):
    """Collapse duplicate ``(parent, item)`` ``CollectionMember`` rows so
    the new ``unique_collection_member`` constraint can be added.

    Existing NeoDB deployments may have duplicate rows from past data
    paths that pre-dated the per-list dedup logic — most commonly when
    two catalog items were merged together and both ended up on the
    same collection. ``Collection.append_item`` short-circuits on the
    happy path, but the merge path bypassed it. Without this dedup the
    ``AddConstraint`` step below would abort the migration.

    Strategy:
    1. Aggregate-pre-filter: ``GROUP BY (parent_id, item_id) HAVING
       COUNT(*) > 1`` runs in one DB pass and returns nothing on a clean
       deployment, so the migration is essentially free in the common
       case.
    2. For each duplicate group, keep the lowest-pk row and merge the
       per-row ``note`` (stored at ``metadata["note"]`` via
       ``jsondata.CharField``) — concatenate distinct non-empty notes
       in pk order separated by a blank line, so the user keeps the
       text they wrote rather than silently losing it.
    3. Delete the surviving duplicates. ``Piece`` parent rows cascade
       via the multi-table-inheritance FK so no orphans remain.
    """
    from django.db.models import Count

    CollectionMember = apps.get_model("journal", "CollectionMember")
    db_alias = schema_editor.connection.alias

    dup_keys = list(
        CollectionMember.objects.using(db_alias)
        .values("parent_id", "item_id")
        .annotate(_c=Count("pk"))
        .filter(_c__gt=1)
        .values_list("parent_id", "item_id")
    )
    if not dup_keys:
        return

    for parent_id, item_id in dup_keys:
        rows = list(
            CollectionMember.objects.using(db_alias)
            .filter(parent_id=parent_id, item_id=item_id)
            .order_by("pk")
        )
        if len(rows) <= 1:
            continue
        keeper = rows[0]
        # Collect distinct non-empty notes in pk order. ``metadata`` is the
        # underlying JSON column; the historical ORM doesn't expose the
        # ``jsondata`` descriptor, so read the JSON dict directly.
        seen_notes: list[str] = []
        for row in rows:
            meta = row.metadata if isinstance(row.metadata, dict) else {}
            n = meta.get("note")
            if isinstance(n, str) and n and n not in seen_notes:
                seen_notes.append(n)
        merged = "\n\n".join(seen_notes) if seen_notes else None
        keeper_meta = dict(keeper.metadata) if isinstance(keeper.metadata, dict) else {}
        if merged != keeper_meta.get("note"):
            if merged is None:
                keeper_meta.pop("note", None)
            else:
                keeper_meta["note"] = merged
            keeper.metadata = keeper_meta
            keeper.save(update_fields=["metadata"])
        # Bulk-delete the surviving duplicates.
        dup_pks = [r.pk for r in rows[1:]]
        CollectionMember.objects.using(db_alias).filter(pk__in=dup_pks).delete()


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
