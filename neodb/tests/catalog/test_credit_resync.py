import pytest

from catalog.common.migrations import resync_duplicate_credits_20260907
from catalog.models import CreditRole, ItemCredit, Movie, Performance
from catalog.models.people import People


@pytest.mark.django_db(databases="__all__")
class TestResyncDuplicateCredits:
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

    def test_collapses_duplicates_and_leaves_clean_items_alone(self):
        person = self._person("Alice", "爱丽丝")
        dup_person = self._movie(["杨力州 "])
        for i, name in enumerate(["杨力州", "Alice"]):
            ItemCredit.objects.create(
                item=dup_person,
                role=CreditRole.Director,
                name=name,
                person=person,
                order=i,
            )
        # Relation-only credit with no metadata counterpart must survive.
        ItemCredit.objects.create(
            item=dup_person,
            role=CreditRole.Actor,
            name="Backfilled",
            person=self._person("Backfilled"),
            order=0,
        )
        dup_name = self._movie(["Bob", "Bob "])
        for i in range(2):
            ItemCredit.objects.create(
                item=dup_name, role=CreditRole.Director, name="Bob", order=i
            )
        clean = self._movie(["Carol"])
        clean.sync_credits_from_metadata()
        clean_edited = Movie.objects.get(pk=clean.pk).edited_time

        resync_duplicate_credits_20260907(batch_size=1, dry_run=True)
        assert dup_person.credits.count() == 3
        assert dup_name.credits.count() == 2

        resync_duplicate_credits_20260907(batch_size=1)

        dup_person = Movie.objects.get(pk=dup_person.pk)
        assert dup_person.director == [person.url]
        directors = list(dup_person.credits.filter(role=CreditRole.Director))
        assert len(directors) == 1
        assert directors[0].person == person
        assert dup_person.actor == []
        assert dup_person.credits.filter(role=CreditRole.Actor).count() == 1

        dup_name = Movie.objects.get(pk=dup_name.pk)
        assert dup_name.director == ["Bob"]
        assert dup_name.credits.count() == 1

        clean = Movie.objects.get(pk=clean.pk)
        assert clean.director == ["Carol"]
        assert clean.credits.count() == 1
        assert clean.edited_time == clean_edited

    def test_distinct_characters_of_one_person_survive(self):
        person = self._person("Star")
        perf = Performance.objects.create(title="Show")
        perf.localized_title = [{"lang": "en", "text": "Show"}]
        perf.actor = [{"name": person.url, "role": "Villain"}]
        perf.save()
        hero = ItemCredit.objects.create(
            item=perf,
            role=CreditRole.Actor,
            name="Star",
            character_name="Hero",
            person=person,
            order=0,
        )
        villain = ItemCredit.objects.create(
            item=perf,
            role=CreditRole.Actor,
            name="Star",
            character_name="Villain",
            person=person,
            order=1,
        )

        resync_duplicate_credits_20260907()

        rows = {c.pk: c.character_name for c in perf.credits.all()}
        assert rows == {hero.pk: "Hero", villain.pk: "Villain"}

    def test_legacy_unlinked_rows_differing_by_whitespace(self):
        m = self._movie(["Alice"])
        for i, name in enumerate(["Alice", "Alice "]):
            ItemCredit.objects.create(
                item=m, role=CreditRole.Director, name=name, order=i
            )

        resync_duplicate_credits_20260907()

        assert [c.name for c in m.credits.all()] == ["Alice"]
