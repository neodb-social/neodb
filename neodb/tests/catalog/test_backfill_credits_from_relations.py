import pytest

from catalog.common.migrations import backfill_credits_from_relations_20260719
from catalog.models import (
    CreditRole,
    Edition,
    ItemCredit,
    Movie,
    People,
    PeopleRole,
    PeopleType,
)
from catalog.models.people import ItemPeopleRelation


def _person(name: str = "Backfill Person") -> People:
    return People.objects.create(
        metadata={"localized_name": [{"lang": "en", "text": name}]},
        people_type=PeopleType.PERSON,
    )


@pytest.mark.django_db(databases="__all__")
class TestBackfillCreditsFromRelations:
    def test_creates_credit_for_unrepresented_relation(self):
        person = _person()
        movie = Movie.objects.create(title="Backfilled Movie")
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.ACTOR, character="Hero"
        )

        backfill_credits_from_relations_20260719()

        credit = ItemCredit.objects.get(item=movie, person=person, role="actor")
        assert credit.name == person.display_name
        assert credit.character_name == "Hero"

    def test_skips_relation_already_represented(self):
        person = _person()
        book = Edition.objects.create(title="Existing Credit Book")
        ItemCredit.objects.create(
            item=book, person=person, role=CreditRole.Author, name="Pre-existing"
        )
        ItemPeopleRelation.objects.create(
            item=book, people=person, role=PeopleRole.AUTHOR
        )

        backfill_credits_from_relations_20260719()

        # No duplicate created; the pre-existing credit is untouched.
        credits = ItemCredit.objects.filter(item=book, person=person, role="author")
        assert credits.count() == 1
        assert credits.get().name == "Pre-existing"

    def test_preserves_extended_roles_1to1(self):
        person = _person()
        movie = Movie.objects.create(title="Voiced Movie")
        book = Edition.objects.create(title="Imprinted Book")
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.VOICE_ACTOR
        )
        # imprint is not a PeopleRole member but is a valid stored value; choices
        # are not DB-enforced. It must round-trip 1:1 into ItemCredit.role.
        ItemPeopleRelation.objects.create(item=book, people=person, role="imprint")

        backfill_credits_from_relations_20260719()

        assert ItemCredit.objects.filter(
            item=movie, person=person, role="voice_actor"
        ).exists()
        assert ItemCredit.objects.filter(
            item=book, person=person, role="imprint"
        ).exists()

    def test_skips_dead_item_and_person(self):
        person = _person()
        merged_target = _person("Merge Target")
        deleted_movie = Movie.objects.create(title="Deleted Movie")
        live_book = Edition.objects.create(title="Live Book")

        # Relation on a soft-deleted item.
        ItemPeopleRelation.objects.create(
            item=deleted_movie, people=person, role=PeopleRole.DIRECTOR
        )
        deleted_movie.is_deleted = True
        deleted_movie.save()

        # Relation on a merged person.
        merged_person = _person("Merged Person")
        merged_person.merge_to(merged_target)
        ItemPeopleRelation.objects.create(
            item=live_book, people=merged_person, role=PeopleRole.AUTHOR
        )

        backfill_credits_from_relations_20260719()

        assert not ItemCredit.objects.filter(item=deleted_movie).exists()
        assert not ItemCredit.objects.filter(person=merged_person).exists()

    def test_idempotent(self):
        person = _person()
        movie = Movie.objects.create(title="Rerun Movie")
        ItemPeopleRelation.objects.create(
            item=movie, people=person, role=PeopleRole.ACTOR
        )

        backfill_credits_from_relations_20260719()
        backfill_credits_from_relations_20260719()

        assert (
            ItemCredit.objects.filter(item=movie, person=person, role="actor").count()
            == 1
        )
