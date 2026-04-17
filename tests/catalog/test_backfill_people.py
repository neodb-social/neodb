from io import StringIO

import pytest
from django.core.management import call_command

from catalog.common import SiteManager, use_local_response
from catalog.models import (
    CreditRole,
    ExternalResource,
    IdType,
    ItemCredit,
    ItemPeopleRelation,
    Movie,
    People,
    PeopleRole,
    PeopleType,
)


@pytest.mark.django_db(databases="__all__")
class TestBackfillPeople:
    def _movie_with_tmdb_resource(self, related_resources):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [
                    {"lang": "en", "text": "Stub Movie"},
                ],
                "director": ["Bryan Cranston"],
            },
            primary_lookup_id_type=IdType.TMDB_Movie,
            primary_lookup_id_value="99999",
        )
        ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Director,
            name="Bryan Cranston",
            order=0,
        )
        resource = ExternalResource.objects.create(
            item=movie,
            id_type=IdType.TMDB_Movie,
            id_value="99999",
            url="https://www.themoviedb.org/movie/99999",
            metadata={
                "localized_title": [{"lang": "en", "text": "Stub Movie"}],
                "director": ["Bryan Cranston"],
                "related_resources": related_resources,
            },
        )
        return movie, resource

    @use_local_response
    def test_rerun_is_idempotent(self):
        """Second run of backfill-people makes no network call and does not
        touch the parent Movie's metadata."""
        people_links = [
            {
                "model": "People",
                "id_type": IdType.TMDB_Person,
                "id_value": "17419",
                "url": "https://www.themoviedb.org/person/17419",
            }
        ]
        movie, resource = self._movie_with_tmdb_resource(people_links)
        original_metadata = dict(movie.metadata)
        original_edited = movie.edited_time

        out = StringIO()
        call_command(
            "catalog",
            "backfill-people",
            "--source",
            IdType.TMDB_Movie,
            stdout=out,
        )
        # A People row for 17419 exists and is linked via ItemCredit.
        person_resource = ExternalResource.objects.get(
            id_type=IdType.TMDB_Person, id_value="17419"
        )
        assert person_resource.item is not None
        assert isinstance(person_resource.item, People)
        credit = movie.credits.get(role=CreditRole.Director)
        assert credit.person_id == person_resource.item.pk

        # Movie metadata/edited_time unchanged after backfill.
        movie.refresh_from_db()
        assert movie.metadata == original_metadata
        assert movie.edited_time == original_edited

        # Count People resources now; a rerun must not create new ones and
        # must not hit the scraper (verified by monkeypatching TMDB_Person
        # after the first run -- any scrape would raise).
        # Replace the TMDB_Person scraper with one that raises, to prove the
        # second run skips the network entirely.
        from catalog.sites.tmdb import TMDB_Person

        orig_scrape = TMDB_Person.scrape

        def _boom(self):
            raise AssertionError("scrape() should not be called on rerun")

        setattr(TMDB_Person, "scrape", _boom)
        try:
            out2 = StringIO()
            call_command(
                "catalog",
                "backfill-people",
                "--source",
                IdType.TMDB_Movie,
                stdout=out2,
            )
            assert "links_already_present=1" in out2.getvalue()
        finally:
            setattr(TMDB_Person, "scrape", orig_scrape)

        # Still exactly one TMDB_Person resource.
        assert (
            ExternalResource.objects.filter(
                id_type=IdType.TMDB_Person, id_value="17419"
            ).count()
            == 1
        )

    def test_rejects_unsupported_source(self):
        err = StringIO()
        call_command(
            "catalog",
            "backfill-people",
            "--source",
            IdType.TMDB_TVEpisode,
            stderr=err,
        )
        assert "--source must be one of" in err.getvalue()

    def test_rejects_missing_source(self):
        err = StringIO()
        call_command("catalog", "backfill-people", stderr=err)
        assert "--source is required" in err.getvalue()


@pytest.mark.django_db(databases="__all__")
class TestFetchLinkedResourcesSibling:
    """Covers the UI scrape path: SiteManager.fetch_linked_resources now does
    sibling-by-name dedup for People CHILD links."""

    def _movie_with_credit_and_person(self, person_resource):
        """Helper: build a Movie, an ItemCredit for 'Bryan Cranston', and a
        pre-existing People tied to that credit via ItemPeopleRelation."""
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Breaking Stub"}],
                "director": ["Bryan Cranston"],
            },
        )
        resource = ExternalResource.objects.create(
            item=movie,
            id_type=IdType.TMDB_Movie,
            id_value="88888",
            url="https://www.themoviedb.org/movie/88888",
        )
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Bryan Cranston"}]},
            people_type=PeopleType.PERSON,
        )
        ExternalResource.objects.create(
            item=person,
            id_type=person_resource["id_type"],
            id_value=person_resource["id_value"],
            url=person_resource["url"],
        )
        ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Director,
            name="Bryan Cranston",
            person=person,
            order=0,
        )
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.DIRECTOR
        )
        return movie, resource, person

    @use_local_response
    def test_reuses_sibling_person_across_sources(self):
        """A People already credited on the parent item is reused by name
        when a different source's People link (same name) is fetched --
        avoiding a duplicate People row. The new resource is scraped so it
        ends up with real metadata (not a placeholder)."""
        movie, resource, existing_person = self._movie_with_credit_and_person(
            {
                "id_type": IdType.DoubanPersonage,
                "id_value": "27228768",
                "url": "https://www.douban.com/personage/27228768/",
            }
        )
        link = {
            "model": "People",
            "id_type": IdType.TMDB_Person,
            "id_value": "17419",
            "url": "https://www.themoviedb.org/person/17419",
            "title": "Bryan Cranston",
        }
        SiteManager.fetch_linked_resources(
            resource, [link], ExternalResource.LinkType.CHILD
        )
        # No new People created; the TMDB_Person resource points at the
        # existing Douban-rooted People.
        assert People.objects.count() == 1
        tmdb_res = ExternalResource.objects.get(
            id_type=IdType.TMDB_Person, id_value="17419"
        )
        assert tmdb_res.item is not None
        assert tmdb_res.item.pk == existing_person.pk
        # Resource is ready (scraped) so a later backfill won't skip it as
        # an empty "already present" placeholder.
        assert tmdb_res.ready
        assert tmdb_res.metadata.get("localized_name")

    @use_local_response
    def test_reuses_sibling_for_url_only_link(self):
        """Douban author/musician links come through as URL-only entries that
        HEAD-resolve to a DoubanPersonage. Sibling dedup must still fire."""
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Stub Movie"}],
                "director": ["成龙"],
            },
        )
        resource = ExternalResource.objects.create(
            item=movie,
            id_type=IdType.DoubanMovie,
            id_value="77777",
            url="https://movie.douban.com/subject/77777/",
        )
        # Pre-create a matching sibling Person already credited on this Movie.
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "zh-cn", "text": "成龙"}]},
            people_type=PeopleType.PERSON,
        )
        ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Director,
            name="成龙",
            person=person,
            order=0,
        )
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.DIRECTOR
        )
        # URL-only link -- no id_type / id_value. Covered by extract_people_
        # links_from_anchors for author/musician redirects.
        link = {
            "model": "People",
            "url": "https://www.douban.com/personage/27228768/",
            "title": "成龙",
        }
        SiteManager.fetch_linked_resources(
            resource, [link], ExternalResource.LinkType.CHILD
        )
        # Sibling reuse -- no second People row.
        assert People.objects.count() == 1
        dp_res = ExternalResource.objects.get(
            id_type=IdType.DoubanPersonage, id_value="27228768"
        )
        assert dp_res.item is not None
        assert dp_res.item.pk == person.pk
        assert dp_res.ready

    def test_same_source_same_name_not_merged(self):
        """Two different TMDB Persons with the same name must not be merged
        into one People row just because their names match -- the candidate
        already has an ExternalResource of the link's id_type (TMDB_Person),
        so sibling dedup must decline."""
        movie, resource, existing_person = self._movie_with_credit_and_person(
            {
                "id_type": IdType.TMDB_Person,
                "id_value": "111",
                "url": "https://www.themoviedb.org/person/111",
            }
        )
        link = {
            "model": "People",
            "id_type": IdType.TMDB_Person,
            "id_value": "222",
            "url": "https://www.themoviedb.org/person/222",
            "title": "Bryan Cranston",
        }
        # Sibling helper must return None (candidate already has
        # TMDB_Person resource), so the shared path must call into
        # linked_site.get_resource_ready. We stub scrape() to avoid network
        # and just observe that it *was* called (proving dedup declined).
        from catalog.sites.tmdb import TMDB_Person

        calls = {"scrape": 0}
        orig_scrape = TMDB_Person.scrape

        def _stub(self):
            calls["scrape"] += 1
            raise RuntimeError("stub")

        setattr(TMDB_Person, "scrape", _stub)
        try:
            SiteManager.fetch_linked_resources(
                resource, [link], ExternalResource.LinkType.CHILD
            )
        finally:
            setattr(TMDB_Person, "scrape", orig_scrape)
        assert calls["scrape"] == 1
        # No new TMDB_Person resource was created by the dedup shortcut
        # (the scrape raised, so nothing else created it either). Crucially,
        # the existing 111 resource was NOT reused under a different id.
        assert not ExternalResource.objects.filter(
            id_type=IdType.TMDB_Person, id_value="222"
        ).exists()
        # And the existing TMDB_Person 111 resource still points at the
        # original People row.
        existing_tmdb = ExternalResource.objects.get(
            id_type=IdType.TMDB_Person, id_value="111"
        )
        assert existing_tmdb.item is not None
        assert existing_tmdb.item.pk == existing_person.pk

    def test_no_sibling_when_link_has_no_name(self):
        """Sibling dedup must not fire when the link omits a name -- the
        shared path should fall through to the normal scrape."""
        movie, resource, existing_person = self._movie_with_credit_and_person(
            {
                "id_type": IdType.DoubanPersonage,
                "id_value": "27228768",
                "url": "https://www.douban.com/personage/27228768/",
            }
        )
        link = {
            "model": "People",
            "id_type": IdType.TMDB_Person,
            "id_value": "17419",
            "url": "https://www.themoviedb.org/person/17419",
            # intentionally no "title"
        }
        from catalog.sites.tmdb import TMDB_Person

        calls = {"scrape": 0}
        orig_scrape = TMDB_Person.scrape

        def _stub(self):
            calls["scrape"] += 1
            raise RuntimeError("stub")

        setattr(TMDB_Person, "scrape", _stub)
        try:
            SiteManager.fetch_linked_resources(
                resource, [link], ExternalResource.LinkType.CHILD
            )
        finally:
            setattr(TMDB_Person, "scrape", orig_scrape)
        # Scrape was attempted (proving the shortcut was bypassed).
        assert calls["scrape"] == 1
