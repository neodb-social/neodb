import pytest

from catalog.models import (
    Edition,
    ItemPeopleRelation,
    People,
    PeopleRole,
    PeopleType,
)

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
            metadata=_DAN_SIMMONS_METADATA,
            people_type=PeopleType.PERSON,
            brief="Science fiction author",
        )

        schema = person.to_schema_org()
        assert schema["@type"] == "Person"
        assert schema["name"] == "Dan Simmons"
        assert schema["description"] == "Science fiction author"

    def test_schema_org_organization(self):
        org = People.objects.create(
            metadata=_BANTAM_BOOKS_METADATA,
            people_type=PeopleType.ORGANIZATION,
            brief="Publishing company",
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
