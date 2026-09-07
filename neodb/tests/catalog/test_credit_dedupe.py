import pytest

from catalog.common.migrations import dedupe_credits_20260907
from catalog.models import CreditRole, ItemCredit, Movie, Performance
from catalog.models.people import People


@pytest.mark.django_db(databases="__all__")
class TestDedupeCredits:
    def _person(self, *names: str) -> People:
        p = People.objects.create(people_type="person", title=names[0])
        p.localized_name = [{"lang": "en", "text": n} for n in names]
        p.save()
        return p

    def _movie(self, director: list[str]) -> Movie:
        m = Movie.objects.create(title="Film")
        m.localized_title = [{"lang": "en", "text": "Film"}]
        m.director = director
        m.save()
        return m

    def _credit(self, item, role, name, person=None, character="", order=0):
        return ItemCredit.objects.create(
            item=item,
            role=role,
            name=name,
            person=person,
            character_name=character,
            order=order,
        )

    def test_collapses_duplicates_and_leaves_the_rest_alone(self):
        person = self._person("Alice", "爱丽丝")
        dup_person = self._movie(["杨力州 "])
        first = self._credit(dup_person, CreditRole.Director, "杨力州", person, order=0)
        self._credit(dup_person, CreditRole.Director, "Alice", person, order=1)
        # Relation-only credit with no metadata counterpart must survive.
        backfilled = self._credit(
            dup_person, CreditRole.Actor, "Backfilled", self._person("Backfilled")
        )
        dup_name = self._movie(["Bob", "Bob "])
        self._credit(dup_name, CreditRole.Director, "Bob", order=0)
        self._credit(dup_name, CreditRole.Director, "Bob", order=1)
        clean = self._movie(["Carol"])
        clean.sync_credits_from_metadata()
        clean_edited = Movie.objects.get(pk=clean.pk).edited_time

        dedupe_credits_20260907(batch_size=1, dry_run=True)
        assert dup_person.credits.count() == 3
        assert dup_name.credits.count() == 2

        dedupe_credits_20260907(batch_size=1)

        assert sorted(dup_person.credits.values_list("pk", flat=True)) == sorted(
            [first.pk, backfilled.pk]
        )
        # names the surviving row: left alone, the next sync links it by name
        assert Movie.objects.get(pk=dup_person.pk).director == ["杨力州 "]
        assert dup_name.credits.count() == 1
        assert Movie.objects.get(pk=dup_name.pk).director == ["Bob", "Bob "]
        assert clean.credits.count() == 1
        assert Movie.objects.get(pk=clean.pk).edited_time == clean_edited

    def test_next_sync_reuses_surviving_row(self):
        person = self._person("Alice", "爱丽丝")
        m = self._movie(["爱丽丝", "Alice"])
        first = self._credit(m, CreditRole.Director, "爱丽丝", person, order=0)
        self._credit(m, CreditRole.Director, "Alice", person, order=1)

        dedupe_credits_20260907()
        m = Movie.objects.get(pk=m.pk)
        # only the name of the deleted row is rewritten
        assert m.director == ["爱丽丝", person.url]
        m.sync_credits_from_metadata()

        assert Movie.objects.get(pk=m.pk).director == [person.url]
        assert list(m.credits.values_list("pk", flat=True)) == [first.pk]

    def test_distinct_characters_of_one_person_survive(self):
        person = self._person("Star")
        perf = Performance.objects.create(title="Show")
        perf.localized_title = [{"lang": "en", "text": "Show"}]
        perf.actor = [{"name": person.url}]
        perf.save()
        hero = self._credit(perf, CreditRole.Actor, "Star", person, "Hero", 0)
        villain = self._credit(perf, CreditRole.Actor, "Star", person, "Villain", 1)

        dedupe_credits_20260907()

        rows = {c.pk: c.character_name for c in perf.credits.all()}
        assert rows == {hero.pk: "Hero", villain.pk: "Villain"}

    def test_ambiguous_name_is_not_rewritten(self):
        director = self._person("Dan")
        perf = Performance.objects.create(title="Show")
        perf.localized_title = [{"lang": "en", "text": "Show"}]
        perf.director = ["Dan"]
        perf.actor = [
            {"name": "Alice", "role": "Hero"},
            {"name": "Alice", "role": "Villain"},
        ]
        perf.save()
        # duplicate directors make the item a candidate
        self._credit(perf, CreditRole.Director, "Dan", director, order=0)
        self._credit(perf, CreditRole.Director, "Dan", director, order=1)
        hero = self._credit(
            perf, CreditRole.Actor, "Alice", self._person("Alice"), "Hero"
        )
        villain = self._credit(
            perf, CreditRole.Actor, "Alice", self._person("Alice"), "Villain", 1
        )

        dedupe_credits_20260907()

        perf = Performance.objects.get(pk=perf.pk)
        assert perf.director == [director.url]
        assert perf.actor == [
            {"name": "Alice", "role": "Hero"},
            {"name": "Alice", "role": "Villain"},
        ]
        assert sorted(
            perf.credits.filter(role="actor").values_list("pk", flat=True)
        ) == sorted([hero.pk, villain.pk])

    def test_legacy_unlinked_rows_differing_by_whitespace(self):
        m = self._movie(["Alice"])
        self._credit(m, CreditRole.Director, "Alice", order=0)
        self._credit(m, CreditRole.Director, "Alice\n", order=1)

        dedupe_credits_20260907()

        assert [c.name for c in m.credits.all()] == ["Alice"]

    def test_unlinked_copy_of_linked_credit_is_dropped(self):
        person = self._person("Alice")
        m = self._movie(["Alice"])
        self._credit(m, CreditRole.Director, "Alice", order=0)
        linked = self._credit(m, CreditRole.Director, "Alice ", person, order=1)

        dedupe_credits_20260907()

        assert list(m.credits.values_list("pk", flat=True)) == [linked.pk]

    def test_two_people_with_the_same_name_are_kept(self):
        m = self._movie(["Alice", "Alice"])
        self._credit(m, CreditRole.Director, "Alice", self._person("Alice"), order=0)
        self._credit(m, CreditRole.Director, "Alice", self._person("Alice"), order=1)

        dedupe_credits_20260907()

        assert m.credits.count() == 2
