"""Race-recovery tests for ExternalResource creation under concurrent fetches."""

import pytest

from catalog.common.sites import ResourceContent
from catalog.models import ExternalResource, IdType


@pytest.mark.django_db(databases="__all__")
class TestUpdateContentRaceRecovery:
    def test_adopts_existing_pk_on_url_conflict(self):
        # Simulate the EGGPLANT-1A4 / NEODB-SOCIAL-4MN race: two workers
        # both miss the get_resource() lookup, build unsaved rows, then
        # one wins the insert. The loser's update_content must adopt the
        # winner's pk instead of raising IntegrityError.
        url = "https://www.imdb.com/title/tt33209584/"
        winner = ExternalResource.objects.create(
            id_type=IdType.IMDB, id_value="tt33209584", url=url
        )
        loser = ExternalResource(id_type=IdType.IMDB, id_value="tt33209584", url=url)
        rc = ResourceContent(
            lookup_ids={IdType.IMDB: "tt33209584"},
            metadata={"title": "S3E2", "preferred_model": "TVEpisode"},
        )

        loser.update_content(rc)

        assert loser.pk == winner.pk
        winner.refresh_from_db()
        assert winner.metadata["title"] == "S3E2"
