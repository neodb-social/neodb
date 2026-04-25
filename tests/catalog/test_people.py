from io import StringIO

import pytest
from django.core.management import call_command

from catalog.common import SiteManager, use_local_response
from catalog.models import (
    Album,
    CreditRole,
    Edition,
    ExternalResource,
    Game,
    ItemCredit,
    ItemPeopleRelation,
    Movie,
    People,
    PeopleRole,
    PeopleType,
    Performance,
)
from catalog.models.common import IdType
from catalog.sites.douban_personage import (
    _parse_douban_date,
    _split_alt_names,
    _split_name,
)
from catalog.sites.wikidata import WikiData, WikidataTypes
from users.models import User

_DAN_SIMMONS_METADATA = {"localized_name": [{"lang": "en", "text": "Dan Simmons"}]}
_BANTAM_BOOKS_METADATA = {"localized_name": [{"lang": "en", "text": "Bantam Books"}]}
_HAYAO_MIYAZAKI_METADATA = {
    "localized_name": [
        {"lang": "ja", "text": "宮崎駿"},
        {"lang": "en", "text": "Hayao Miyazaki"},
    ]
}


@pytest.mark.django_db(databases="__all__")
class TestPeople:
    def test_create_person(self):
        person = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="American science fiction author",
        )
        assert person.is_person
        assert not person.is_organization
        assert person.display_name == "Dan Simmons"
        assert person.uuid
        assert person.url == f"/person/{person.uuid}"

    def test_create_organization(self):
        org = People.objects.create(
            metadata=_BANTAM_BOOKS_METADATA,
            people_type=PeopleType.ORGANIZATION,
            brief="Publishing company",
        )
        assert not org.is_person
        assert org.is_organization
        assert org.display_name == "Bantam Books"
        assert org.url == f"/organization/{org.uuid}"

    def test_localized_names(self):
        person = People.objects.create(
            people_type=PeopleType.PERSON,
            metadata=_HAYAO_MIYAZAKI_METADATA,
        )
        assert person.display_name == "Hayao Miyazaki"
        assert "宮崎駿" in person.additional_names

    def test_people_merge(self):
        person1 = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="Author of Hyperion",
        )
        person2 = People.objects.create(
            title="Daniel Simmons",
            people_type=PeopleType.PERSON,
            brief="Science fiction writer",
        )

        person1.merge_to(person2)
        assert person1.merged_to_item == person2
        assert person1.final_item == person2

    def test_people_merge_resolve(self):
        person1 = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA, people_type=PeopleType.PERSON
        )
        person2 = People.objects.create(
            title="Daniel Simmons", people_type=PeopleType.PERSON
        )
        person3 = People.objects.create(
            title="D. Simmons", people_type=PeopleType.PERSON
        )

        person1.merge_to(person2)
        person2.merge_to(person3)
        resolved = People.get_by_url(person1.url, True)
        assert resolved == person3

    def test_people_merge_with_links(self):
        book = Edition.objects.create(title="Hyperion")
        person1 = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA, people_type=PeopleType.PERSON
        )
        person2 = People.objects.create(
            title="Daniel Simmons", people_type=PeopleType.PERSON
        )

        # Create link for person1
        link1 = ItemPeopleRelation.objects.create(
            item=book, people=person1, role=PeopleRole.AUTHOR
        )

        # Merge person1 to person2
        person1.merge_to(person2)

        # Link should now point to person2
        link1.refresh_from_db()
        assert link1.people == person2

        # Should have only one link for this item-role combination
        links = ItemPeopleRelation.objects.filter(item=book, role=PeopleRole.AUTHOR)
        assert links.count() == 1

    def test_people_merge_duplicate_links(self):
        book = Edition.objects.create(title="Hyperion")
        person1 = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA, people_type=PeopleType.PERSON
        )
        person2 = People.objects.create(
            title="Daniel Simmons", people_type=PeopleType.PERSON
        )

        # Create same role links for both people
        ItemPeopleRelation.objects.create(
            item=book, people=person1, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=book, people=person2, role=PeopleRole.AUTHOR
        )

        # Merge person1 to person2
        person1.merge_to(person2)

        # Should have only one link remaining (duplicate removed)
        relations = ItemPeopleRelation.objects.filter(item=book, role=PeopleRole.AUTHOR)
        assert relations.count() == 1
        r = relations.first()
        assert r is not None
        assert r.people == person2

    def test_people_soft_delete(self):
        person = People.objects.create(people_type=PeopleType.PERSON)

        assert person.is_deletable()
        person.delete(soft=True)
        assert person.is_deleted

    def test_people_cannot_delete_with_links(self):
        book = Edition.objects.create(title="Hyperion")
        person = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA, people_type=PeopleType.PERSON
        )

        ItemPeopleRelation.objects.create(
            item=book, people=person, role=PeopleRole.AUTHOR
        )

        assert not person.is_deletable()

    def test_schema_org_person(self):
        person = People.objects.create(
            metadata={
                **_DAN_SIMMONS_METADATA,
                "localized_bio": [{"lang": "en", "text": "Science fiction author"}],
            },
            people_type=PeopleType.PERSON,
        )

        schema = person.to_schema_org()
        assert schema["@type"] == "Person"
        assert schema["name"] == "Dan Simmons"
        assert schema["description"] == "Science fiction author"

    def test_schema_org_organization(self):
        org = People.objects.create(
            metadata={
                **_BANTAM_BOOKS_METADATA,
                "localized_bio": [{"lang": "en", "text": "Publishing company"}],
            },
            people_type=PeopleType.ORGANIZATION,
        )

        schema = org.to_schema_org()
        assert schema["@type"] == "Organization"
        assert schema["name"] == "Bantam Books"

    def test_item_get_people_by_role(self):
        """Test that Item.get_people_by_role returns People queryset instead of relations"""
        book = Edition.objects.create(title="Hyperion")

        # Create author
        author = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="Science fiction author",
        )

        # Create publisher
        publisher = People.objects.create(
            metadata=_BANTAM_BOOKS_METADATA,
            people_type=PeopleType.ORGANIZATION,
            brief="Publishing company",
        )

        # Create relations
        ItemPeopleRelation.objects.create(
            item=book, people=author, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=book, people=publisher, role=PeopleRole.PUBLISHER
        )

        # Test that get_people_by_role returns People queryset
        authors = book.get_people_by_role(PeopleRole.AUTHOR)
        publishers = book.get_people_by_role(PeopleRole.PUBLISHER)

        # Should return People objects, not ItemPeopleRelation objects
        assert authors.count() == 1
        assert isinstance(authors.first(), People)
        assert authors.first() == author

        assert publishers.count() == 1
        assert isinstance(publishers.first(), People)
        assert publishers.first() == publisher

        # Test with non-existent role
        directors = book.get_people_by_role(PeopleRole.DIRECTOR)
        assert directors.count() == 0

    def test_item_merge_with_people_relations(self):
        """Test that people relations are merged when items are merged"""
        # Create two books
        book1 = Edition.objects.create(title="Hyperion First Edition")
        book2 = Edition.objects.create(title="Hyperion Second Edition")

        # Create people
        author = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="Science fiction author",
        )
        publisher = People.objects.create(
            metadata=_BANTAM_BOOKS_METADATA,
            people_type=PeopleType.ORGANIZATION,
            brief="Publishing company",
        )

        # Create relations for book1
        ItemPeopleRelation.objects.create(
            item=book1, people=author, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=book1, people=publisher, role=PeopleRole.PUBLISHER
        )

        # Verify initial state
        assert book1.people_relations.count() == 2
        assert book2.people_relations.count() == 0

        # Merge book1 to book2
        book1.merge_to(book2)

        # Verify relations were transferred
        assert book1.people_relations.count() == 0  # Relations moved from book1
        assert book2.people_relations.count() == 2  # Relations moved to book2

        # Verify the actual relations
        book2_authors = book2.get_people_by_role(PeopleRole.AUTHOR)
        book2_publishers = book2.get_people_by_role(PeopleRole.PUBLISHER)

        assert book2_authors.count() == 1
        assert book2_authors.first() == author
        assert book2_publishers.count() == 1
        assert book2_publishers.first() == publisher

    def test_item_merge_with_duplicate_people_relations(self):
        """Test merging items when both have relations to the same people with same roles"""
        # Create two books
        book1 = Edition.objects.create(title="Hyperion First Edition")
        book2 = Edition.objects.create(title="Hyperion Second Edition")

        # Create author
        author = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="Science fiction author",
        )

        # Create same author relation for both books
        ItemPeopleRelation.objects.create(
            item=book1,
            people=author,
            role=PeopleRole.ACTOR,
            character="Kassad",  # book1 has character info
        )
        ItemPeopleRelation.objects.create(
            item=book2,
            people=author,
            role=PeopleRole.ACTOR,
            # book2 has no character info
        )

        # Verify initial state
        assert book1.people_relations.count() == 1
        assert book2.people_relations.count() == 1

        # Merge book1 to book2
        book1.merge_to(book2)

        # Verify only one relation remains (duplicate removed)
        assert book1.people_relations.count() == 0
        assert book2.people_relations.count() == 1

        # Verify character info was preserved from book1
        remaining_relation = book2.people_relations.first()
        assert remaining_relation is not None
        assert remaining_relation.people == author
        assert remaining_relation.role == PeopleRole.ACTOR
        assert remaining_relation.character == "Kassad"

    def test_bio(self):
        person = People.objects.create(
            metadata={
                "localized_name": [{"lang": "en", "text": "Douglas Adams"}],
                "localized_bio": [
                    {"lang": "en", "text": "English author and humourist"},
                ],
            },
            people_type=PeopleType.PERSON,
        )
        assert person.display_description == "English author and humourist"
        schema = person.to_schema_org()
        assert schema["description"] == "English author and humourist"

    def test_birth_death_dates(self):
        person = People.objects.create(
            metadata={
                "localized_name": [{"lang": "en", "text": "Douglas Adams"}],
                "birth_date": "1952-03-11",
                "death_date": "2001-05-11",
            },
            people_type=PeopleType.PERSON,
        )
        assert person.birth_date == "1952-03-11"
        assert person.death_date == "2001-05-11"
        schema = person.to_schema_org()
        assert schema["birthDate"] == "1952-03-11"
        assert schema["deathDate"] == "2001-05-11"

    def test_official_site(self):
        person = People.objects.create(
            metadata={
                "localized_name": [{"lang": "en", "text": "Test Person"}],
                "official_site": "https://example.com",
            },
            people_type=PeopleType.PERSON,
        )
        assert person.official_site == "https://example.com"

    def test_lookup_id_type_choices(self):
        choices = People.lookup_id_type_choices()
        id_types = [c[0] for c in choices]
        assert IdType.IMDB.value in id_types
        assert IdType.TMDB_Person.value in id_types
        assert IdType.WikiData.value in id_types
        assert IdType.Goodreads_Author.value in id_types
        assert IdType.Spotify_Artist.value in id_types
        assert IdType.OpenLibrary_Author.value in id_types
        assert IdType.IGDB_Company.value in id_types
        assert IdType.DoubanPersonage.value in id_types
        # Should not include movie-specific types
        assert IdType.TMDB_Movie.value not in id_types

    def test_lookup_id_cleanup_imdb(self):
        # Valid IMDb person ID
        t, v = People.lookup_id_cleanup(IdType.IMDB.value, "nm0000129")
        assert t == IdType.IMDB.value
        assert v == "nm0000129"

        # Invalid IMDb ID (movie, not person)
        t, v = People.lookup_id_cleanup(IdType.IMDB.value, "tt1234567")
        assert t is None
        assert v is None

        # Strips whitespace
        t, v = People.lookup_id_cleanup(IdType.IMDB.value, " nm0000129 ")
        assert v == "nm0000129"

        # Non-IMDB types pass through normally
        t, v = People.lookup_id_cleanup(IdType.WikiData.value, "Q42")
        assert t == IdType.WikiData.value
        assert v == "Q42"

    def test_metadata_copy_list(self):
        assert "localized_name" in People.METADATA_COPY_LIST
        assert "localized_bio" in People.METADATA_COPY_LIST
        assert "birth_date" in People.METADATA_COPY_LIST
        assert "death_date" in People.METADATA_COPY_LIST
        assert "official_site" in People.METADATA_COPY_LIST
        assert "people_type" in People.METADATA_COPY_LIST
        # Should NOT include localized_title or localized_description
        assert "localized_title" not in People.METADATA_COPY_LIST
        assert "localized_description" not in People.METADATA_COPY_LIST

    def test_people_form_includes_people_type(self):
        from catalog.forms import CatalogForms

        form_cls = CatalogForms["People"]
        assert "people_type" in form_cls.base_fields


@pytest.mark.django_db(databases="__all__", transaction=True)
class TestPeopleCreateView:
    def _login(self):
        from django.test import Client

        user = User.register(email="creator@example.com", username="creator")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        return client

    def test_create_form_prefills_organization_type(self):
        client = self._login()
        response = client.get(
            "/catalog/create/People?title=Acme&people_type=organization"
        )
        assert response.status_code == 200
        form = response.context["form"]
        assert form.initial.get("people_type") == "organization"
        assert form.initial["localized_name"][0]["text"] == "Acme"

    def test_create_form_prefills_person_type(self):
        client = self._login()
        response = client.get("/catalog/create/People?people_type=person")
        assert response.status_code == 200
        assert response.context["form"].initial.get("people_type") == "person"

    def test_create_form_ignores_invalid_people_type(self):
        client = self._login()
        response = client.get("/catalog/create/People?people_type=bogus")
        assert response.status_code == 200
        assert "people_type" not in response.context["form"].initial

    def test_create_form_jsondata_fields_have_no_deferred_initial(self):
        """jsondata fields must not surface Django's DEFERRED sentinel as form initial."""
        client = self._login()
        response = client.get("/catalog/create/People")
        assert response.status_code == 200
        form = response.context["form"]
        for name in ("birth_date", "death_date", "official_site"):
            initial = form[name].value()
            assert initial in (None, "", [], {}), (
                f"{name} initial leaked non-empty value: {initial!r}"
            )
            assert "Deferred" not in str(initial)


@pytest.mark.django_db(databases="__all__")
class TestItemCredit:
    def test_create_credit_without_person(self):
        book = Edition.objects.create(title="Hyperion")
        credit = ItemCredit.objects.create(
            item=book,
            role=CreditRole.Author,
            name="Dan Simmons",
            order=0,
        )
        assert credit.person is None
        assert credit.name == "Dan Simmons"
        assert credit.role == CreditRole.Author
        assert credit.character_name == ""
        assert str(credit) == f"Dan Simmons (author) on {book}"

    def test_create_credit_with_person(self):
        book = Edition.objects.create(title="Hyperion")
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Dan Simmons"}]},
            people_type=PeopleType.PERSON,
        )
        credit = ItemCredit.objects.create(
            item=book,
            role=CreditRole.Author,
            name="Dan Simmons",
            person=person,
            order=0,
        )
        assert credit.person == person
        assert credit.name == "Dan Simmons"

    def test_credit_with_character_name(self):
        movie = Edition.objects.create(title="The Dark Knight")
        credit = ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Actor,
            name="Christian Bale",
            character_name="Batman",
            order=0,
        )
        assert credit.character_name == "Batman"

    def test_credit_ordering(self):
        movie = Edition.objects.create(title="The Dark Knight")
        c1 = ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Christian Bale", order=0
        )
        c2 = ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Heath Ledger", order=1
        )
        c3 = ItemCredit.objects.create(
            item=movie, role=CreditRole.Director, name="Christopher Nolan", order=0
        )
        credits = list(movie.credits.all())
        assert credits == [c1, c2, c3]

    def test_person_deletion_nullifies_credit(self):
        book = Edition.objects.create(title="Hyperion")
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Dan Simmons"}]},
            people_type=PeopleType.PERSON,
        )
        credit = ItemCredit.objects.create(
            item=book,
            role=CreditRole.Author,
            name="Dan Simmons",
            person=person,
            order=0,
        )
        person.delete(soft=False)
        credit.refresh_from_db()
        assert credit.person is None
        assert credit.name == "Dan Simmons"

    def test_item_deletion_cascades_credits(self):
        book = Edition.objects.create(title="Hyperion")
        ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Dan Simmons", order=0
        )
        book_id = book.pk
        book.delete(soft=False)
        assert ItemCredit.objects.filter(item_id=book_id).count() == 0


@pytest.mark.django_db(databases="__all__")
class TestPopulateCredits:
    def test_populate_from_movie(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "The Matrix"}],
                "director": ["Lana Wachowski", "Lilly Wachowski"],
                "playwright": ["Lana Wachowski"],
                "actor": ["Keanu Reeves", "Laurence Fishburne"],
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        credits = list(movie.credits.all())
        directors = [c for c in credits if c.role == CreditRole.Director]
        assert len(directors) == 2
        assert directors[0].name == "Lana Wachowski"
        assert directors[0].order == 0
        assert directors[1].name == "Lilly Wachowski"
        assert directors[1].order == 1
        playwrights = [c for c in credits if c.role == CreditRole.Playwright]
        assert len(playwrights) == 1
        actors = [c for c in credits if c.role == CreditRole.Actor]
        assert len(actors) == 2

    def test_populate_from_edition(self):
        book = Edition.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Dune"}],
                "author": ["Frank Herbert"],
                "translator": ["Someone"],
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        authors = list(book.credits.filter(role=CreditRole.Author))
        assert len(authors) == 1
        assert authors[0].name == "Frank Herbert"
        translators = list(book.credits.filter(role=CreditRole.Translator))
        assert len(translators) == 1

    def test_no_duplicates_on_rerun(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test Movie"}],
                "director": ["Test Director"],
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        assert movie.credits.count() == 1
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        assert movie.credits.count() == 1

    def test_role_credits_property(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test Movie"}],
            }
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Director, name="Dir A", order=0
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Act A", order=0
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Act B", order=1
        )
        # Re-fetch to clear cached_property from save()->update_index()
        movie = Movie.objects.get(pk=movie.pk)
        rc = movie.role_credits
        assert len(rc.get("director", [])) == 1
        assert len(rc.get("actor", [])) == 2
        assert rc["director"][0].name == "Dir A"


@pytest.mark.django_db(databases="__all__")
class TestLinkCredits:
    def test_link_matching_credits(self):
        book = Edition.objects.create(title="Hyperion")
        credit = ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Dan Simmons", order=0
        )
        person = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
        )
        person.link_matching_credits()
        credit.refresh_from_db()
        assert credit.person == person

    def test_link_creates_people_relation(self):
        book = Edition.objects.create(title="Hyperion")
        ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Dan Simmons", order=0
        )
        person = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
        )
        person.link_matching_credits()
        assert ItemPeopleRelation.objects.filter(
            item=book, people=person, role=PeopleRole.AUTHOR
        ).exists()

    def test_no_link_when_name_differs(self):
        book = Edition.objects.create(title="Hyperion")
        credit = ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Daniel Simmons", order=0
        )
        person = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
        )
        person.link_matching_credits()
        credit.refresh_from_db()
        assert credit.person is None

    def test_link_credits_bulk(self):
        """link_credits migration function links all unlinked credits."""
        from catalog.common.migrations import link_credits_20260412

        book = Edition.objects.create(title="Hyperion")
        credit = ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Dan Simmons", order=0
        )
        People.objects.create(
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
        )
        link_credits_20260412()
        credit.refresh_from_db()
        assert credit.person is not None


@pytest.mark.django_db(databases="__all__")
class TestOrganizationSupport:
    def test_create_organization_with_type(self):
        org = People.objects.create(
            metadata={
                "localized_name": [{"lang": "en", "text": "Nintendo"}],
                "people_type": "organization",
            },
            people_type=PeopleType.ORGANIZATION,
        )
        assert org.is_organization
        assert not org.is_person
        assert org.display_name == "Nintendo"

    def test_populate_credits_from_game(self):
        game = Game.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Zelda"}],
                "developer": ["Nintendo EAD"],
                "publisher": ["Nintendo"],
                "designer": ["Shigeru Miyamoto"],
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        devs = list(game.credits.filter(role=CreditRole.Developer))
        assert len(devs) == 1
        assert devs[0].name == "Nintendo EAD"
        pubs = list(game.credits.filter(role=CreditRole.Publisher))
        assert len(pubs) == 1
        assert pubs[0].name == "Nintendo"
        designers = list(game.credits.filter(role=CreditRole.Designer))
        assert len(designers) == 1
        assert designers[0].name == "Shigeru Miyamoto"

    def test_populate_credits_single_string_field(self):
        """pub_house is a single string, not a list."""
        book = Edition.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test Book"}],
                "pub_house": "Penguin Books",
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        pubs = list(book.credits.filter(role=CreditRole.Publisher))
        assert len(pubs) == 1
        assert pubs[0].name == "Penguin Books"

    def test_populate_credits_album_company(self):
        album = Album.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Abbey Road"}],
                "company": ["Apple Records", "EMI"],
            }
        )
        out = StringIO()
        call_command("catalog", "migrate", "--name", "populate_credits", stdout=out)
        labels = list(album.credits.filter(role=CreditRole.RecordLabel))
        assert len(labels) == 2
        assert labels[0].name == "Apple Records"


@pytest.mark.django_db(databases="__all__")
class TestMergeCredits:
    def test_item_merge_reparents_credits(self):
        movie1 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie v1"}]}
        )
        movie2 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie v2"}]}
        )
        c1 = ItemCredit.objects.create(
            item=movie1, role=CreditRole.Director, name="Director A", order=0
        )
        movie1.merge_to(movie2)
        c1.refresh_from_db()
        assert c1.item == movie2

    def test_item_merge_deduplicates_credits(self):
        movie1 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie v1"}]}
        )
        movie2 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie v2"}]}
        )
        ItemCredit.objects.create(
            item=movie1, role=CreditRole.Director, name="Same Dir", order=0
        )
        ItemCredit.objects.create(
            item=movie2, role=CreditRole.Director, name="Same Dir", order=0
        )
        movie1.merge_to(movie2)
        assert movie2.credits.filter(role=CreditRole.Director).count() == 1

    def test_people_merge_reparents_credited_items(self):
        book = Edition.objects.create(title="Book")
        person1 = People.objects.create(
            metadata=_DAN_SIMMONS_METADATA, people_type=PeopleType.PERSON
        )
        person2 = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Daniel Simmons"}]},
            people_type=PeopleType.PERSON,
        )
        credit = ItemCredit.objects.create(
            item=book,
            role=CreditRole.Author,
            name="Dan Simmons",
            person=person1,
            order=0,
        )
        person1.merge_to(person2)
        credit.refresh_from_db()
        assert credit.person == person2


@pytest.mark.django_db(databases="__all__")
class TestDisplayFallback:
    def test_display_description_from_bio(self):
        person = People.objects.create(
            metadata={
                "localized_name": [{"lang": "en", "text": "Test"}],
                "localized_bio": [{"lang": "en", "text": "Bio text"}],
            },
            people_type=PeopleType.PERSON,
        )
        assert person.display_description == "Bio text"

    def test_wikidata_org_type_mapping(self):
        """Wikidata org types should map to People model."""
        for org_type in [
            WikidataTypes.BUSINESS_ENTERPRISE,
            WikidataTypes.PUBLISHER,
            WikidataTypes.RECORD_LABEL,
            WikidataTypes.VIDEO_GAME_DEVELOPER,
            WikidataTypes.FILM_PRODUCTION_COMPANY,
        ]:
            entity_data = {
                "id": "Q999",
                "claims": {
                    "P31": [
                        {
                            "mainsnak": {
                                "datavalue": {"value": {"id": org_type}},
                            }
                        }
                    ]
                },
            }
            wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999")
            model = wiki_site._determine_entity_type(entity_data)
            assert model == People, f"{org_type} should map to People"


class TestDoubanPersonageHelpers:
    """Tests for helper functions in douban_personage module."""

    def test_split_name_cn_and_en(self):
        cn, en = _split_name("成龙 Jackie Chan")
        assert cn == "成龙"
        assert en == "Jackie Chan"

    def test_split_name_cn_and_en_with_middledot(self):
        cn, en = _split_name("马丁·斯科塞斯 Martin Scorsese")
        assert cn == "马丁·斯科塞斯"
        assert en == "Martin Scorsese"

    def test_split_name_cn_only(self):
        cn, en = _split_name("张艺谋")
        assert cn == "张艺谋"
        assert en is None

    def test_split_name_en_only(self):
        cn, en = _split_name("Leonardo DiCaprio")
        assert cn is None
        assert en == "Leonardo DiCaprio"

    def test_parse_douban_date_full(self):
        assert _parse_douban_date("1954年4月7日") == "1954-04-07"

    def test_parse_douban_date_year_month(self):
        assert _parse_douban_date("1954年4月") == "1954-04"

    def test_parse_douban_date_year_only(self):
        assert _parse_douban_date("1954年") == "1954"

    def test_parse_douban_date_empty(self):
        assert _parse_douban_date("") is None

    def test_parse_douban_date_zero_padded(self):
        assert _parse_douban_date("2000年1月2日") == "2000-01-02"

    def test_split_alt_names(self):
        result = _split_alt_names("房仕龙(本名) / 陈港生(原名) / 元楼(前艺名)")
        assert result == ["房仕龙", "陈港生", "元楼"]

    def test_split_alt_names_empty(self):
        assert _split_alt_names("") == []

    def test_split_alt_names_no_annotations(self):
        result = _split_alt_names("Kong-sang Chan / Pao Pao")
        assert result == ["Kong-sang Chan", "Pao Pao"]


@pytest.mark.django_db(databases="__all__")
class TestDoubanPersonage:
    def test_parse_personage_url(self):
        t_url = "https://www.douban.com/personage/27228768/"
        p = SiteManager.get_site_cls_by_id_type(IdType.DoubanPersonage)
        assert p is not None
        assert p.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == "27228768"
        assert site.url is not None
        assert "personage/27228768" in site.url

    @use_local_response
    def test_scrape_personage_url(self):
        """Test scraping Chen Kaige from personage URL."""
        t_url = "https://www.douban.com/personage/27228768/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.item is not None
        item = site.resource.item
        assert isinstance(item, People)
        assert item.display_name == "陈凯歌"
        names = [n["text"] for n in item.localized_name]
        assert "Kaige Chen" in names
        assert item.birth_date == "1952-08-12"
        assert item.imdb == "nm0155280"

    def test_work_urls(self, monkeypatch):
        from catalog.sites import douban_personage as douban_personage_module

        monkeypatch.setattr(
            douban_personage_module, "DOUBAN_PERSONAGE_WORKS_PAGE_SIZE", 2
        )
        payloads = [
            {
                "r": 0,
                "data": {
                    "items": [
                        {"subject": {"url": "https://movie.douban.com/subject/11/"}},
                        {"subject": {"url": "https://music.douban.com/subject/22/"}},
                    ]
                },
            },
            {
                "r": 0,
                "data": {
                    "items": [
                        {"subject": {"url": "https://movie.douban.com/subject/33/"}},
                    ]
                },
            },
            {
                "r": 0,
                "data": {
                    "items": [
                        {"subject": {"url": "https://movie.douban.com/subject/11/"}},
                        {"subject": {"url": "https://movie.douban.com/subject/44/"}},
                    ]
                },
            },
            {"r": 0, "data": {"items": []}},
        ]
        urls: list[str] = []

        class _FakeResp:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class _FakeDL:
            headers = {}

            def __init__(self, url, headers=None, timeout=None):
                urls.append(url)

            def download(self):
                return _FakeResp(payloads.pop(0))

        monkeypatch.setattr(douban_personage_module, "BasicDownloader", _FakeDL)

        site = douban_personage_module.DoubanPersonage(id_value="27228768")
        assert site.fetch_people_work_urls() == [
            "https://movie.douban.com/subject/11/",
            "https://movie.douban.com/subject/33/",
            "https://movie.douban.com/subject/44/",
        ]
        assert "released=1" in urls[0]
        assert "start=0" in urls[0]
        assert "released=1" in urls[1]
        assert "start=2" in urls[1]
        assert "released=0" in urls[2]
        assert "start=0" in urls[2]
        assert "released=0" in urls[3]
        assert "start=2" in urls[3]


@pytest.mark.django_db(databases="__all__")
class TestTMDBPerson:
    def test_parse(self):
        t_url = "https://www.themoviedb.org/person/17419"
        p = SiteManager.get_site_cls_by_id_type(IdType.TMDB_Person)
        assert p is not None
        assert p.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == "17419"

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.themoviedb.org/person/17419"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.item is not None
        item = site.resource.item
        assert isinstance(item, People)
        names = [n["text"] for n in item.localized_name]
        assert len(names) >= 1
        assert item.birth_date is not None
        # Should have external IDs
        assert site.resource.other_lookup_ids


@pytest.mark.django_db(databases="__all__")
class TestGoodreadsAuthor:
    def test_parse(self):
        t_url = "https://www.goodreads.com/author/show/874602"
        p = SiteManager.get_site_cls_by_id_type(IdType.Goodreads_Author)
        assert p is not None
        assert p.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == "874602"

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.goodreads.com/author/show/874602"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.item is not None
        item = site.resource.item
        assert isinstance(item, People)
        assert "Ursula" in item.display_name or "Le Guin" in item.display_name


@pytest.mark.django_db(databases="__all__")
class TestSpotifyArtist:
    def test_parse(self):
        t_id_value = "4Z8W4fKeB5YxbusRsdQVPb"
        t_url = f"https://open.spotify.com/artist/{t_id_value}"
        p = SiteManager.get_site_cls_by_id_type(IdType.Spotify_Artist)
        assert p is not None
        assert p.validate_url(t_url)
        assert p.DEFAULT_MODEL == People
        assert p.WIKI_PROPERTY_ID == "P1902"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == t_id_value

    def test_parse_regional_url(self):
        """intl-xx regional prefix should still parse."""
        t_id_value = "4Z8W4fKeB5YxbusRsdQVPb"
        t_url = f"https://open.spotify.com/intl-ja/artist/{t_id_value}"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == t_id_value


@pytest.mark.django_db(databases="__all__")
class TestIGDBCompany:
    def test_parse(self):
        t_id_value = "valve"
        t_url = f"https://www.igdb.com/companies/{t_id_value}"
        p = SiteManager.get_site_cls_by_id_type(IdType.IGDB_Company)
        assert p is not None
        assert p.validate_url(t_url)
        assert p.DEFAULT_MODEL == People
        assert p.WIKI_PROPERTY_ID == "P9650"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.id_value == t_id_value


@pytest.mark.django_db(databases="__all__")
class TestSyncCreditsFromMetadata:
    def test_sync_movie_credits(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test"}],
                "director": ["Dir A"],
                "playwright": ["Writer B"],
                "actor": ["Actor C", "Actor D"],
            }
        )
        movie.sync_credits_from_metadata()
        assert movie.credits.filter(role=CreditRole.Director).count() == 1
        assert movie.credits.filter(role=CreditRole.Actor).count() == 2
        assert movie.credits.filter(role=CreditRole.Playwright).count() == 1

    def test_sync_preserves_order(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test"}],
                "actor": ["First", "Second", "Third"],
            }
        )
        movie.sync_credits_from_metadata()
        actors = list(movie.credits.filter(role=CreditRole.Actor).order_by("order"))
        assert [a.name for a in actors] == ["First", "Second", "Third"]
        assert [a.order for a in actors] == [0, 1, 2]

    def test_sync_idempotent(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test"}],
                "director": ["Dir A"],
            }
        )
        movie.sync_credits_from_metadata()
        movie.sync_credits_from_metadata()
        assert movie.credits.count() == 1

    def test_sync_dict_values_with_character(self):
        perf = Performance.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Show"}],
                "actor": [
                    {"name": "Alice", "role": "Hamlet"},
                    {"name": "Bob", "role": None},
                ],
            }
        )
        perf.sync_credits_from_metadata()
        actors = list(perf.credits.filter(role=CreditRole.Actor).order_by("order"))
        assert len(actors) == 2
        assert actors[0].character_name == "Hamlet"
        assert actors[1].character_name == ""

    def test_sync_single_string_field(self):
        book = Edition.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Book"}],
                "pub_house": "Penguin",
            }
        )
        book.sync_credits_from_metadata()
        pubs = list(book.credits.filter(role=CreditRole.Publisher))
        assert len(pubs) == 1
        assert pubs[0].name == "Penguin"

    def test_sync_skips_empty_names(self):
        movie = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Test"}],
                "director": ["", "Real Director", ""],
            }
        )
        movie.sync_credits_from_metadata()
        assert movie.credits.count() == 1
        credit = movie.credits.first()
        assert credit is not None
        assert credit.name == "Real Director"


@pytest.mark.django_db(databases="__all__")
class TestRoleCreditsAndAPI:
    def test_role_credits_grouped(self):
        movie = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "M"}]}
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Director, name="Dir", order=0
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Act1", order=0
        )
        ItemCredit.objects.create(
            item=movie, role=CreditRole.Actor, name="Act2", order=1
        )
        movie = Movie.objects.get(pk=movie.pk)
        rc = movie.role_credits
        assert len(rc["director"]) == 1
        assert len(rc["actor"]) == 2
        assert rc["director"][0].name == "Dir"

    def test_api_credits(self):
        movie = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "M"}]}
        )
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Dir"}]},
            people_type=PeopleType.PERSON,
        )
        ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Director,
            name="Dir",
            person=person,
            order=0,
        )
        ItemCredit.objects.create(
            item=movie,
            role=CreditRole.Actor,
            name="Unknown Actor",
            order=0,
        )
        movie = Movie.objects.get(pk=movie.pk)
        api = movie.api_credits
        assert len(api) == 2
        dir_credit = next(c for c in api if c.role == CreditRole.Director)
        assert dir_credit.name == "Dir"
        assert dir_credit.person is not None
        act_credit = next(c for c in api if c.role == CreditRole.Actor)
        assert act_credit.person is None


@pytest.mark.django_db(databases="__all__")
class TestPeopleFindByName:
    def test_exact_match(self):
        People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "John Smith"}]},
            people_type=PeopleType.PERSON,
        )
        results = People.find_by_name("John Smith", exact=True)
        assert len(results) == 1
        assert results[0].display_name == "John Smith"

    def test_exact_no_match(self):
        People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "John Smith"}]},
            people_type=PeopleType.PERSON,
        )
        results = People.find_by_name("Jane Smith", exact=True)
        assert len(results) == 0

    def test_partial_match(self):
        People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "John Smith"}]},
            people_type=PeopleType.PERSON,
        )
        results = People.find_by_name("Smith", exact=False)
        assert len(results) == 1

    def test_excludes_deleted(self):
        p = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Deleted Person"}]},
            people_type=PeopleType.PERSON,
        )
        p.is_deleted = True
        p.save()
        results = People.find_by_name("Deleted Person", exact=True)
        assert len(results) == 0


@pytest.mark.django_db(databases="__all__")
class TestRelatedItemsByRole:
    def test_groups_by_role(self):
        movie1 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie 1"}]}
        )
        movie2 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Movie 2"}]}
        )
        book = Edition.objects.create(title="Book 1")
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Multi-talent"}]},
            people_type=PeopleType.PERSON,
        )
        ItemPeopleRelation.objects.create(
            item=movie1, people=person, role=PeopleRole.DIRECTOR
        )
        ItemPeopleRelation.objects.create(
            item=movie2, people=person, role=PeopleRole.ACTOR
        )
        ItemPeopleRelation.objects.create(
            item=book, people=person, role=PeopleRole.AUTHOR
        )
        groups = person.related_items_by_role
        assert len(groups) == 3
        roles = [g[0] for g in groups]
        assert PeopleRole.DIRECTOR in roles
        assert PeopleRole.ACTOR in roles
        assert PeopleRole.AUTHOR in roles

    def test_excludes_deleted_items(self):
        movie = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Deleted Movie"}]}
        )
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Person"}]},
            people_type=PeopleType.PERSON,
        )
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.DIRECTOR
        )
        movie.is_deleted = True
        movie.save()
        groups = person.related_items_by_role
        assert len(groups) == 0


@pytest.mark.django_db(databases="__all__")
class TestCreditRoleMapping:
    def test_all_credit_roles_map(self):
        """All CreditRole values that have a PeopleRole equivalent should map correctly."""
        mapped = {
            CreditRole.Author: PeopleRole.AUTHOR,
            CreditRole.Director: PeopleRole.DIRECTOR,
            CreditRole.Actor: PeopleRole.ACTOR,
            CreditRole.Playwright: PeopleRole.PLAYWRIGHT,
            CreditRole.Composer: PeopleRole.COMPOSER,
            CreditRole.Artist: PeopleRole.ARTIST,
            CreditRole.Designer: PeopleRole.DESIGNER,
            CreditRole.Performer: PeopleRole.PERFORMER,
            CreditRole.Host: PeopleRole.HOST,
            CreditRole.Publisher: PeopleRole.PUBLISHER,
            CreditRole.Developer: PeopleRole.DEVELOPER,
        }
        for credit_role, people_role in mapped.items():
            result = People._credit_role_to_people_role(credit_role)
            assert result == people_role, f"{credit_role} should map to {people_role}"

    def test_unmapped_role_returns_none(self):
        result = People._credit_role_to_people_role("nonexistent_role")
        assert result is None


@pytest.mark.django_db(databases="__all__")
class TestLinkCreditsMultiLingual:
    def test_link_by_alternate_name(self):
        """Credits should link when matching any localized_name entry."""
        book = Edition.objects.create(title="Book")
        credit = ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="Hayao Miyazaki", order=0
        )
        person = People.objects.create(
            metadata=_HAYAO_MIYAZAKI_METADATA,
            people_type=PeopleType.PERSON,
        )
        person.link_matching_credits()
        credit.refresh_from_db()
        assert credit.person == person

    def test_link_by_japanese_name(self):
        book = Edition.objects.create(title="Book")
        credit = ItemCredit.objects.create(
            item=book, role=CreditRole.Author, name="宮崎駿", order=0
        )
        person = People.objects.create(
            metadata=_HAYAO_MIYAZAKI_METADATA,
            people_type=PeopleType.PERSON,
        )
        person.link_matching_credits()
        credit.refresh_from_db()
        assert credit.person == person


class TestExtractPersonageLinks:
    def test_extract_personage_links(self):
        """_extract_personage_links should extract personage IDs from page HTML."""
        from lxml import html

        from catalog.sites.douban_movie import _extract_personage_links

        page_html = """
        <div id="info">
          <span>导演</span><span class="attrs">
            <a href="https://www.douban.com/personage/12345/">Director</a>
          </span>
          <span>主演</span><span class="attrs">
            <a href="https://www.douban.com/personage/67890/">Actor One</a>
            /
            <a href="https://www.douban.com/personage/11111/">Actor Two</a>
          </span>
        </div>
        """
        content = html.fromstring(page_html)
        resources = _extract_personage_links(content)
        assert len(resources) == 3
        assert resources[0]["id_value"] == "12345"
        assert resources[0]["id_type"] == IdType.DoubanPersonage
        assert "personage/12345" in resources[0]["url"]
        assert resources[1]["id_value"] == "67890"
        assert resources[2]["id_value"] == "11111"

    def test_extract_deduplicates(self):
        """Same person appearing as director and actor should not be duplicated."""
        from lxml import html

        from catalog.sites.douban_movie import _extract_personage_links

        page_html = """
        <div id="info">
          <span>导演</span><span class="attrs">
            <a href="https://www.douban.com/personage/12345/">Person</a>
          </span>
          <span>主演</span><span class="attrs">
            <a href="https://www.douban.com/personage/12345/">Person</a>
          </span>
        </div>
        """
        content = html.fromstring(page_html)
        resources = _extract_personage_links(content)
        assert len(resources) == 1

    def test_ignores_non_personage_links(self):
        """Old celebrity links should be ignored."""
        from lxml import html

        from catalog.sites.douban_movie import _extract_personage_links

        page_html = """
        <div id="info">
          <span>导演</span><span class="attrs">
            <a href="https://movie.douban.com/celebrity/99999/">Old Dir</a>
          </span>
        </div>
        """
        content = html.fromstring(page_html)
        resources = _extract_personage_links(content)
        assert len(resources) == 0


class TestExtractPeopleLinksFromAnchors:
    def test_author_links(self):
        """Author links should be extracted with their full URL."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="https://book.douban.com/author/4608425">Author One</a>
          <a href="https://book.douban.com/author/9999999/">Author Two</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 2
        assert resources[0]["url"] == "https://book.douban.com/author/4608425/"
        assert resources[1]["url"] == "https://book.douban.com/author/9999999/"
        # Author links don't have id_type (resolved via redirect)
        assert "id_type" not in resources[0]

    def test_musician_links(self):
        """Musician links should be extracted with their full URL."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="https://music.douban.com/musician/104916/">Artist</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 1
        assert resources[0]["url"] == "https://music.douban.com/musician/104916/"

    def test_personage_links_direct(self):
        """Personage links should be extracted with id_type and id_value."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="https://www.douban.com/personage/30098574/">Person</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 1
        assert resources[0]["id_type"] == IdType.DoubanPersonage
        assert resources[0]["id_value"] == "30098574"
        assert resources[0]["url"] == "https://www.douban.com/personage/30098574/"

    def test_mixed_link_types(self):
        """Mix of personage, author, and musician links."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="https://www.douban.com/personage/11111/">Direct Person</a>
          <a href="https://book.douban.com/author/22222">Book Author</a>
          <a href="https://music.douban.com/musician/33333/">Musician</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 3
        assert resources[0]["id_type"] == IdType.DoubanPersonage
        assert resources[1]["url"] == "https://book.douban.com/author/22222/"
        assert resources[2]["url"] == "https://music.douban.com/musician/33333/"

    def test_relative_urls(self):
        """Relative author and musician paths should be normalized to full URLs."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="/author/4608425">Author (relative)</a>
          <a href="/musician/104916/">Musician (relative)</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 2
        assert resources[0]["url"] == "https://book.douban.com/author/4608425/"
        assert resources[1]["url"] == "https://music.douban.com/musician/104916/"

    def test_deduplicates(self):
        """Same author via relative and absolute URL should not be duplicated."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="/author/4608425">Name 1</a>
          <a href="https://book.douban.com/author/4608425/">Name 1</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 1

    def test_limit(self):
        """Should respect the limit parameter."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        anchors_html = "".join(
            f'<a href="https://www.douban.com/personage/{i}/">P{i}</a>'
            for i in range(1, 20)
        )
        fragment = html.fromstring(f"<div>{anchors_html}</div>")
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors, limit=5)
        assert len(resources) == 5

    def test_ignores_unrelated_links(self):
        """Non-person links should be ignored."""
        from lxml import html

        from catalog.sites.douban import extract_people_links_from_anchors

        fragment = html.fromstring("""
        <div>
          <a href="https://book.douban.com/subject/12345/">A Book</a>
          <a href="https://movie.douban.com/celebrity/99999/">Celebrity</a>
          <a href="https://example.com/">External</a>
        </div>
        """)
        anchors = fragment.findall(".//a")
        resources = extract_people_links_from_anchors(anchors)
        assert len(resources) == 0


@pytest.mark.django_db(databases="__all__")
class TestItemMergeCreditsAdvanced:
    def test_merge_preserves_character_name(self):
        movie1 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "v1"}]}
        )
        movie2 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "v2"}]}
        )
        ItemCredit.objects.create(
            item=movie1,
            role=CreditRole.Actor,
            name="Actor",
            character_name="Batman",
            order=0,
        )
        ItemCredit.objects.create(
            item=movie2,
            role=CreditRole.Actor,
            name="Actor",
            order=0,
        )
        movie1.merge_to(movie2)
        credit = movie2.credits.get(role=CreditRole.Actor, name="Actor")
        assert credit.character_name == "Batman"

    def test_merge_preserves_person_link(self):
        movie1 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "v1"}]}
        )
        movie2 = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "v2"}]}
        )
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor"}]},
            people_type=PeopleType.PERSON,
        )
        ItemCredit.objects.create(
            item=movie1,
            role=CreditRole.Actor,
            name="Actor",
            person=person,
            order=0,
        )
        ItemCredit.objects.create(
            item=movie2,
            role=CreditRole.Actor,
            name="Actor",
            order=0,
        )
        movie1.merge_to(movie2)
        credit = movie2.credits.get(role=CreditRole.Actor, name="Actor")
        assert credit.person == person


@pytest.mark.django_db(databases="__all__")
class TestTMDBCombinedCreditUrls:
    def test_parses_and_dedupes(self, monkeypatch):
        from catalog.sites import tmdb as tmdb_site

        payload = {
            "cast": [
                {"id": 11, "media_type": "movie"},
                {"id": 22, "media_type": "tv"},
                {"id": 11, "media_type": "movie"},
                {"id": 33, "media_type": "person"},
                {"id": None, "media_type": "movie"},
            ],
            "crew": [
                {"id": 44, "media_type": "movie"},
                {"id": 22, "media_type": "tv"},
            ],
        }

        class _FakeResp:
            def json(self):
                return payload

        class _FakeDL:
            def __init__(self, url):
                pass

            def download(self):
                return _FakeResp()

        monkeypatch.setattr(tmdb_site, "BasicDownloader", _FakeDL)
        urls = tmdb_site.TMDB_Person(id_value="999").fetch_people_work_urls()
        assert urls == [
            "https://www.themoviedb.org/movie/11",
            "https://www.themoviedb.org/movie/44",
            "https://www.themoviedb.org/tv/22",
        ]

    def test_returns_empty_on_error(self, monkeypatch):
        from catalog.sites import tmdb as tmdb_site

        class _FakeDL:
            def __init__(self, url):
                pass

            def download(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(tmdb_site, "BasicDownloader", _FakeDL)
        assert tmdb_site.TMDB_Person(id_value="999").fetch_people_work_urls() == []


@pytest.mark.django_db(databases="__all__")
class TestFetchWorksForPersonTask:
    def test_enqueues_per_url(self, monkeypatch):
        import sys

        from catalog.jobs.people_works import fetch_works_for_person_task
        from catalog.sites.tmdb import TMDB_Person

        people_works_module = sys.modules["catalog.jobs.people_works"]
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor X"}]},
            people_type=PeopleType.PERSON,
        )
        person.primary_lookup_id_type = IdType.WikiData
        person.primary_lookup_id_value = "Q123"
        person.save()
        ExternalResource.objects.create(
            item=person,
            id_type=IdType.TMDB_Person,
            id_value="17419",
            url="https://www.themoviedb.org/person/17419",
        )

        user = User.register(email="worker@example.com", username="worker")
        calls: list[tuple[str, bool]] = []
        requested_ids: list[str] = []
        monkeypatch.setattr(
            people_works_module,
            "enqueue_fetch",
            lambda url, is_refetch=False, user=None: calls.append((url, is_refetch)),
        )

        def _fake_fetch_urls(site):
            requested_ids.append(site.id_value)
            return [
                "https://www.themoviedb.org/movie/11",
                "https://www.themoviedb.org/tv/22",
            ]

        monkeypatch.setattr(TMDB_Person, "fetch_people_work_urls", _fake_fetch_urls)

        fetch_works_for_person_task(person.uuid, user.pk)

        assert requested_ids == ["17419"]
        assert len(calls) == 2
        assert {c[0] for c in calls} == {
            "https://www.themoviedb.org/movie/11",
            "https://www.themoviedb.org/tv/22",
        }
        assert all(not c[1] for c in calls)

    def test_enqueues_credit_link_after_fetch_jobs(self, monkeypatch):
        import sys

        from catalog.jobs.people_works import (
            fetch_works_for_person_task,
            link_people_works_task,
        )
        from catalog.sites.tmdb import TMDB_Person

        people_works_module = sys.modules["catalog.jobs.people_works"]
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor Async"}]},
            people_type=PeopleType.PERSON,
        )
        ExternalResource.objects.create(
            item=person,
            id_type=IdType.TMDB_Person,
            id_value="17419",
            url="https://www.themoviedb.org/person/17419",
        )

        user = User.register(email="worker5@example.com", username="worker5")
        immediate_links = []
        queued = []
        monkeypatch.setattr(
            people_works_module,
            "enqueue_fetch",
            lambda url, is_refetch=False, user=None: f"fetch-{url.rsplit('/', 1)[-1]}",
        )
        monkeypatch.setattr(
            People,
            "link_matching_credits",
            lambda self: immediate_links.append(self.pk),
        )

        class _FakeQueue:
            def enqueue(self, fn, *args, **kwargs):
                queued.append((fn, args, kwargs))

        monkeypatch.setattr(
            people_works_module.django_rq, "get_queue", lambda name: _FakeQueue()
        )
        monkeypatch.setattr(
            TMDB_Person,
            "fetch_people_work_urls",
            lambda site: [
                "https://www.themoviedb.org/movie/11",
                "https://www.themoviedb.org/tv/22",
            ],
        )

        fetch_works_for_person_task(person.uuid, user.pk)

        assert immediate_links == []
        assert len(queued) == 1
        fn, args, kwargs = queued[0]
        assert fn is link_people_works_task
        assert args == (person.uuid, user.pk)
        assert kwargs["depends_on"].dependencies == ["fetch-11", "fetch-22"]
        assert kwargs["depends_on"].allow_failure is True

    def test_enqueues_douban_personage_urls(self, monkeypatch):
        import sys

        from catalog.jobs.people_works import fetch_works_for_person_task
        from catalog.sites.douban_personage import DoubanPersonage

        people_works_module = sys.modules["catalog.jobs.people_works"]
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor D"}]},
            people_type=PeopleType.PERSON,
        )
        ExternalResource.objects.create(
            item=person,
            id_type=IdType.DoubanPersonage,
            id_value="27228768",
            url="https://www.douban.com/personage/27228768/",
        )

        user = User.register(email="worker3@example.com", username="worker3")
        calls: list[tuple[str, bool]] = []
        requested_ids: list[str] = []
        monkeypatch.setattr(
            people_works_module,
            "enqueue_fetch",
            lambda url, is_refetch=False, user=None: calls.append((url, is_refetch)),
        )

        def _fake_fetch_urls(site):
            requested_ids.append(site.id_value)
            return [
                "https://movie.douban.com/subject/11/",
                "https://movie.douban.com/subject/22/",
            ]

        monkeypatch.setattr(DoubanPersonage, "fetch_people_work_urls", _fake_fetch_urls)

        fetch_works_for_person_task(person.uuid, user.pk)

        assert requested_ids == ["27228768"]
        assert len(calls) == 2
        assert {c[0] for c in calls} == {
            "https://movie.douban.com/subject/11/",
            "https://movie.douban.com/subject/22/",
        }
        assert all(not c[1] for c in calls)

    def test_noop_without_tmdb_id(self, monkeypatch):
        import sys

        from catalog.jobs.people_works import fetch_works_for_person_task
        from catalog.sites.tmdb import TMDB_Person

        people_works_module = sys.modules["catalog.jobs.people_works"]
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor Y"}]},
            people_type=PeopleType.PERSON,
        )
        person.tmdb_person = "17419"
        person.save()
        user = User.register(email="worker2@example.com", username="worker2")
        called = []
        requested_ids = []
        monkeypatch.setattr(
            people_works_module, "enqueue_fetch", lambda *a, **kw: called.append(a)
        )

        def _fake_fetch_urls(site):
            requested_ids.append(site.id_value)
            return ["https://www.themoviedb.org/movie/11"]

        monkeypatch.setattr(TMDB_Person, "fetch_people_work_urls", _fake_fetch_urls)
        fetch_works_for_person_task(person.uuid, user.pk)
        assert requested_ids == []
        assert called == []

    def test_accepts_legacy_tmdb_id_arg(self, monkeypatch):
        import sys

        from catalog.jobs.people_works import fetch_works_for_person_task
        from catalog.sites.tmdb import TMDB_Person

        people_works_module = sys.modules["catalog.jobs.people_works"]
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor Legacy"}]},
            people_type=PeopleType.PERSON,
        )
        user = User.register(email="worker4@example.com", username="worker4")
        calls = []
        requested_ids = []
        monkeypatch.setattr(
            people_works_module, "enqueue_fetch", lambda *a, **kw: calls.append(a)
        )

        def _fake_fetch_urls(site):
            requested_ids.append(site.id_value)
            return ["https://www.themoviedb.org/movie/11"]

        monkeypatch.setattr(TMDB_Person, "fetch_people_work_urls", _fake_fetch_urls)

        fetch_works_for_person_task(person.uuid, user.pk, "17419")

        assert requested_ids == ["17419"]
        assert calls == [("https://www.themoviedb.org/movie/11",)]


@pytest.mark.django_db(databases="__all__", transaction=True)
class TestFetchPeopleWorksView:
    def _make_person(
        self, *, with_tmdb: bool = True, with_douban: bool = False
    ) -> People:
        person = People.objects.create(
            metadata={"localized_name": [{"lang": "en", "text": "Actor Z"}]},
            people_type=PeopleType.PERSON,
        )
        if with_tmdb:
            ExternalResource.objects.create(
                item=person,
                id_type=IdType.TMDB_Person,
                id_value="17419",
                url="https://www.themoviedb.org/person/17419",
            )
        if with_douban:
            ExternalResource.objects.create(
                item=person,
                id_type=IdType.DoubanPersonage,
                id_value="27228768",
                url="https://www.douban.com/personage/27228768/",
            )
        return person

    def _url(self, person: People) -> str:
        return f"{person.url}/fetch_people_works"

    def test_unauthenticated_redirects(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        person = self._make_person()
        client = Client()
        response = client.post(self._url(person))
        assert response.status_code in (302, 301)

    def test_non_person_uuid_returns_404(self):
        from django.test import Client

        movie = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "M"}]}
        )
        user = User.register(email="viewer1@example.com", username="viewer1")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(f"{movie.url}/fetch_people_works")
        assert response.status_code == 404

    def test_person_without_tmdb_returns_400(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        person = self._make_person(with_tmdb=False)
        user = User.register(email="viewer2@example.com", username="viewer2")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(self._url(person))
        assert response.status_code == 400

    def test_primary_tmdb_id_without_resource_returns_400(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        person = self._make_person(with_tmdb=False)
        person.tmdb_person = "17419"
        person.save()
        user = User.register(email="viewer5@example.com", username="viewer5")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(self._url(person))
        assert response.status_code == 400

    def test_enqueues_and_locks(self, monkeypatch):
        import sys

        from django.core.cache import cache
        from django.test import Client

        from catalog.jobs.people_works import fetch_works_for_person_task

        cache.clear()
        person = self._make_person()
        tmdb_resource = person.external_resources.get(id_type=IdType.TMDB_Person)
        user = User.register(email="viewer3@example.com", username="viewer3")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        enqueued: list[tuple] = []

        class _FakeQueue:
            def enqueue(self, fn, *args, **kwargs):
                enqueued.append((fn, args))

        people_works_module = sys.modules["catalog.jobs.people_works"]
        monkeypatch.setattr(
            people_works_module.django_rq, "get_queue", lambda name: _FakeQueue()
        )

        response = client.post(self._url(person), {"resource_id": tmdb_resource.pk})
        assert response.status_code == 302
        assert len(enqueued) == 1
        assert enqueued[0][0] is fetch_works_for_person_task
        assert enqueued[0][1] == (
            person.uuid,
            user.pk,
            IdType.TMDB_Person,
            "17419",
        )
        assert cache.get(f"_fetch_works_lock:{person.pk}") == 1

        response2 = client.post(self._url(person), {"resource_id": tmdb_resource.pk})
        assert response2.status_code == 302
        assert len(enqueued) == 1  # lock prevents re-enqueue

    def test_enqueues_douban_resource(self, monkeypatch):
        import sys

        from django.core.cache import cache
        from django.test import Client

        from catalog.jobs.people_works import fetch_works_for_person_task

        cache.clear()
        person = self._make_person(with_tmdb=False, with_douban=True)
        douban_resource = person.external_resources.get(id_type=IdType.DoubanPersonage)
        user = User.register(email="viewer8@example.com", username="viewer8")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        enqueued: list[tuple] = []

        class _FakeQueue:
            def enqueue(self, fn, *args, **kwargs):
                enqueued.append((fn, args))

        people_works_module = sys.modules["catalog.jobs.people_works"]
        monkeypatch.setattr(
            people_works_module.django_rq, "get_queue", lambda name: _FakeQueue()
        )

        response = client.post(self._url(person), {"resource_id": douban_resource.pk})
        assert response.status_code == 302
        assert len(enqueued) == 1
        assert enqueued[0][0] is fetch_works_for_person_task
        assert enqueued[0][1] == (
            person.uuid,
            user.pk,
            IdType.DoubanPersonage,
            "27228768",
        )

    def test_invalid_resource_id_returns_400(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        person = self._make_person()
        user = User.register(email="viewer6@example.com", username="viewer6")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(self._url(person), {"resource_id": "999999"})
        assert response.status_code == 400

    def test_edit_page_shows_pull_button_for_tmdb_resource(self):
        from django.test import Client

        person = self._make_person()
        user = User.register(email="viewer7@example.com", username="viewer7")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(f"{person.url}/edit")
        assert response.status_code == 200
        assert b"pull works" in response.content
        assert b'name="resource_id"' in response.content

    def test_edit_page_shows_pull_button_for_douban_resource(self):
        from django.test import Client

        person = self._make_person(with_tmdb=False, with_douban=True)
        user = User.register(email="viewer9@example.com", username="viewer9")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(f"{person.url}/edit")
        assert response.status_code == 200
        assert b"pull works" in response.content
        assert b'name="resource_id"' in response.content

    def test_protected_person_forbidden_for_non_staff(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        person = self._make_person()
        person.is_protected = True
        person.save()
        user = User.register(email="viewer4@example.com", username="viewer4")
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(self._url(person))
        assert response.status_code == 403
