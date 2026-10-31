"""Race-recovery tests for ExternalResource creation under concurrent fetches."""

import threading
import time
from unittest.mock import patch

import pytest
from django.db import connections

from catalog.common.sites import ResourceContent
from catalog.models import ExternalResource, IdType, Movie
from catalog.sites.douban_movie import DoubanMovie


@pytest.mark.django_db(databases="__all__")
class TestUpdateContentRaceRecovery:
    def test_adopts_existing_pk_on_url_conflict(self):
        # Simulate the insert race: two workers
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


@pytest.mark.django_db(databases="__all__", transaction=True)
class TestConcurrentMatchAndLink:
    def test_two_fetches_of_one_url_create_one_item(self):
        url = "https://movie.douban.com/subject/26782621/"
        content = ResourceContent(
            metadata={
                "localized_title": [{"lang": "en", "text": "Race"}],
                "preferred_model": "Movie",
            },
            lookup_ids={IdType.IMDB: "tt1958026"},
        )
        create = Movie.create_from_external_resource
        first_create = threading.Event()
        errors = []

        def slow_create(p):
            if not first_create.is_set():
                first_create.set()
                # hold job A between matching and linking, where job B used
                # to find the resource without an item
                time.sleep(1)
            return create(p)

        def fetch(wait: threading.Event | None):
            try:
                if wait:
                    wait.wait(10)
                DoubanMovie(url).get_resource_ready(auto_link=False)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        with (
            patch.object(DoubanMovie, "scrape", return_value=content),
            patch.object(
                Movie, "create_from_external_resource", side_effect=slow_create
            ),
        ):
            a = threading.Thread(target=fetch, args=(None,))
            b = threading.Thread(target=fetch, args=(first_create,))
            a.start()
            b.start()
            a.join(30)
            b.join(30)

        assert not errors
        assert Movie.objects.count() == 1
        res = ExternalResource.objects.get(url=url)
        assert res.item == Movie.objects.get()
