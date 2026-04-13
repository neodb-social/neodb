from io import StringIO

import pytest
from django.core.management import call_command

from catalog.models import (
    Album,
    CreditRole,
    Edition,
    Game,
    ItemCredit,
    ItemPeopleRelation,
    Movie,
    People,
    PeopleRole,
    PeopleType,
)
from catalog.models.common import IdType
from catalog.sites.wikidata import WikiData, WikidataTypes

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
        assert person.url == f"/people/{person.uuid}"

    def test_create_organization(self):
        org = People.objects.create(
            metadata=_BANTAM_BOOKS_METADATA,
            people_type=PeopleType.ORGANIZATION,
            brief="Publishing company",
        )
        assert not org.is_person
        assert org.is_organization
        assert org.display_name == "Bantam Books"

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
        # Should NOT include localized_title or localized_description
        assert "localized_title" not in People.METADATA_COPY_LIST
        assert "localized_description" not in People.METADATA_COPY_LIST


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
