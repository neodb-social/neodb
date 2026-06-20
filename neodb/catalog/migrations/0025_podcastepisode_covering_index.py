from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0024_verifiedcreator"),
    ]

    operations = [
        # Add item_ptr as a covering column so child_item_ids reads episode ids
        # via an index-only scan (EGGPLANT-1EA).
        migrations.RemoveIndex(
            model_name="podcastepisode",
            name="catalog_pod_program_2e4327_idx",
        ),
        migrations.AddIndex(
            model_name="podcastepisode",
            index=models.Index(
                fields=["program", "pub_date"],
                include=("item_ptr",),
                name="podcast_ep_prog_pubdate_cov",
            ),
        ),
    ]
