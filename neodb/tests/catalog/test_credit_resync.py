import pytest

from catalog.common.migrations import resync_duplicate_credits_20260907
from catalog.models import CreditRole, ItemCredit, Movie
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
        dup_name = self._movie(["Bob", "Bob "])
        for i in range(2):
            ItemCredit.objects.create(
                item=dup_name, role=CreditRole.Director, name="Bob", order=i
            )
        clean = self._movie(["Carol"])
        clean.sync_credits_from_metadata()
        clean_edited = Movie.objects.get(pk=clean.pk).edited_time

        resync_duplicate_credits_20260907(batch_size=1, dry_run=True)
        assert dup_person.credits.count() == 2
        assert dup_name.credits.count() == 2

        resync_duplicate_credits_20260907(batch_size=1)

        dup_person = Movie.objects.get(pk=dup_person.pk)
        assert dup_person.director == [person.url]
        credits = list(dup_person.credits.all())
        assert len(credits) == 1
        assert credits[0].person == person

        dup_name = Movie.objects.get(pk=dup_name.pk)
        assert dup_name.director == ["Bob"]
        assert dup_name.credits.count() == 1

        clean = Movie.objects.get(pk=clean.pk)
        assert clean.director == ["Carol"]
        assert clean.credits.count() == 1
        assert clean.edited_time == clean_edited
