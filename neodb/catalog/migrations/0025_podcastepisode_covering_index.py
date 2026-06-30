from django.contrib.postgres.operations import (
    AddIndexConcurrently,
    RemoveIndexConcurrently,
)
from django.db import migrations, models


class Migration(migrations.Migration):
    """Swap the PodcastEpisode (program, pub_date) index for a covering one.

    item_ptr as an INCLUDE column lets child_item_ids read episode ids via
    an index-only scan (EGGPLANT-1EA). The covering index is created before
    the old one is dropped so (program, pub_date) is never left without an
    index during the migration.

    Both ops use CREATE/DROP INDEX CONCURRENTLY, which must run outside a
    transaction (atomic = False) so the table is never blocked.
    """

    atomic = False

    dependencies = [
        ("catalog", "0024_verifiedcreator"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="podcastepisode",
            index=models.Index(
                fields=["program", "pub_date"],
                include=("item_ptr",),
                name="podcast_ep_prog_pubdate_cov",
            ),
        ),
        RemoveIndexConcurrently(
            model_name="podcastepisode",
            name="catalog_pod_program_2e4327_idx",
        ),
    ]
