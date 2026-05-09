from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


def _index_op(model: str) -> AddIndexConcurrently:
    return AddIndexConcurrently(
        model_name=model,
        index=models.Index(fields=["remote_id"], name=f"{model}_remote_id_idx"),
    )


def _add_remote_id(model: str) -> migrations.AddField:
    return migrations.AddField(
        model_name=model,
        name="remote_id",
        field=models.CharField(default=None, max_length=200, null=True),
    )


class Migration(migrations.Migration):
    """Add and index ``remote_id`` for federated journal pieces.

    - ``Collection`` (List subclass) gets the field and an index now to
      support inbound federation by AP id.
    - ``Shelf`` and ``Tag`` (also List subclasses) inherit the field via
      the abstract ``List``; the column is added but not indexed because
      federation for them is deferred to a follow-up PR.
    - ``Comment``, ``Note``, ``Rating``, ``Review``, ``Debris`` (Content
      subclasses) already have the column; this migration only adds an
      index on each so URL-paste lookups are not seq scans.

    All indexes are built with ``CREATE INDEX CONCURRENTLY``, which
    requires running outside of a transaction (``atomic = False``).
    Adding a nullable column on Postgres 11+ is metadata-only, so the
    interleaved ``AddField`` operations remain safe under non-atomic mode.
    """

    atomic = False

    dependencies = [
        ("journal", "0008_steamimporter"),
        ("journal", "0002_shelflogentry_metadata"),
    ]

    operations = [
        _add_remote_id("collection"),
        _add_remote_id("shelf"),
        _add_remote_id("tag"),
        _index_op("collection"),
        _index_op("comment"),
        _index_op("note"),
        _index_op("rating"),
        _index_op("review"),
        _index_op("debris"),
    ]
