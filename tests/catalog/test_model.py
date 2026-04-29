import pytest

from catalog.models import (
    CreditRole,
    Edition,
    Item,
    ItemCredit,
    Movie,
    Performance,
    PerformanceProduction,
    TVSeason,
    TVShow,
)
from catalog.models.people import People
from common.models.jsondata import decrypt_str, encrypt_str


@pytest.mark.django_db(databases="__all__")
class TestCatalog:
    def test_merge(self):
        hyperion_hardcover = Edition.objects.create(title="Hyperion")
        hyperion_hardcover.pages = 481
        hyperion_hardcover.isbn = "9780385249492"
        hyperion_hardcover.save()
        hyperion_print = Edition.objects.create(title="Hyperion")
        hyperion_print.pages = 500
        hyperion_print.isbn = "9780553283686"
        hyperion_print.save()

        hyperion_hardcover.merge_to(hyperion_print)
        assert hyperion_hardcover.merged_to_item == hyperion_print

    def test_merge_resolve(self):
        hyperion_hardcover = Edition.objects.create(title="Hyperion")
        hyperion_hardcover.pages = 481
        hyperion_hardcover.isbn = "9780385249492"
        hyperion_hardcover.save()
        hyperion_print = Edition.objects.create(title="Hyperion")
        hyperion_print.pages = 500
        hyperion_print.isbn = "9780553283686"
        hyperion_print.save()
        hyperion_ebook = Edition(title="Hyperion")
        hyperion_ebook.asin = "B0043M6780"
        hyperion_ebook.save()

        hyperion_hardcover.merge_to(hyperion_print)
        hyperion_print.merge_to(hyperion_ebook)
        resolved = Item.get_by_url(hyperion_hardcover.url, True)
        assert resolved == hyperion_ebook

    def test_encypted_field(self):
        o = "Hello, World!"
        e = encrypt_str(o)
        d = decrypt_str(e)
        assert o == d


@pytest.mark.django_db(databases="__all__")
class TestSyncCreditsFromMetadata:
    def _make_movie(self) -> Movie:
        m = Movie.objects.create(title="Test Film")
        m.localized_title = [{"lang": "en", "text": "Test Film"}]
        m.save()
        return m

    @pytest.mark.parametrize("prefix", ["/people/", "/person/", "/organization/"])
    def test_resolves_people_url_path(self, prefix):
        person = People.objects.create(
            people_type="person",
            title="Jane Director",
        )
        person.localized_name = [{"lang": "en", "text": "Jane Director"}]
        person.save()
        m = self._make_movie()
        m.director = [f"{prefix}{person.uuid}"]
        m.save()
        m.sync_credits_from_metadata()
        credit = m.credits.get(role=CreditRole.Director)
        assert credit.person == person
        assert credit.name == "Jane Director"

    def test_resolves_full_url(self):
        person = People.objects.create(
            people_type="person",
            title="Alfred Hitchcock",
        )
        person.localized_name = [{"lang": "en", "text": "Alfred Hitchcock"}]
        person.save()
        m = self._make_movie()
        m.director = [f"https://example.org/person/{person.uuid}"]
        m.save()
        m.sync_credits_from_metadata()
        assert m.credits.get(role=CreditRole.Director).person == person

    def test_plain_name_leaves_person_unlinked(self):
        m = self._make_movie()
        m.director = ["Someone Unlisted"]
        m.save()
        m.sync_credits_from_metadata()
        credit = m.credits.get(role=CreditRole.Director)
        assert credit.person is None
        assert credit.name == "Someone Unlisted"

    def test_stale_credits_pruned_for_managed_roles(self):
        m = self._make_movie()
        m.director = ["Alice", "Bob"]
        m.save()
        m.sync_credits_from_metadata()
        assert {c.name for c in m.credits.filter(role=CreditRole.Director)} == {
            "Alice",
            "Bob",
        }
        m.director = ["Alice"]
        m.save()
        m.sync_credits_from_metadata()
        assert {c.name for c in m.credits.filter(role=CreditRole.Director)} == {"Alice"}

    def test_canonicalizes_jsondata_when_credit_is_linked(self):
        person = People.objects.create(people_type="person", title="Pre-linked")
        person.localized_name = [{"lang": "en", "text": "Pre-linked"}]
        person.save()
        m = self._make_movie()
        # Simulate legacy state: jsondata has plain name, ItemCredit is linked.
        m.director = ["Pre-linked"]
        m.save()
        ItemCredit.objects.create(
            item=m,
            role=CreditRole.Director,
            name="Pre-linked",
            person=person,
            order=0,
        )
        m.sync_credits_from_metadata()
        m.refresh_from_db()
        assert m.director == [person.url]
        credit = m.credits.get(role=CreditRole.Director)
        assert credit.person == person
        assert credit.name == "Pre-linked"

    def test_canonicalizes_dict_entry_name_field(self):
        person = People.objects.create(people_type="person", title="Star")
        person.localized_name = [{"lang": "en", "text": "Star"}]
        person.save()
        from catalog.models import Performance

        perf = Performance.objects.create(title="Show")
        perf.localized_title = [{"lang": "en", "text": "Show"}]
        perf.actor = [{"name": "Star", "role": "Hero"}]
        perf.save()
        ItemCredit.objects.create(
            item=perf,
            role=CreditRole.Actor,
            name="Star",
            character_name="Hero",
            person=person,
            order=0,
        )
        perf.sync_credits_from_metadata()
        perf.refresh_from_db()
        assert perf.actor == [{"name": person.url, "role": "Hero"}]

    def test_unmanaged_role_credits_preserved(self):
        m = self._make_movie()
        ItemCredit.objects.create(
            item=m,
            role=CreditRole.Composer,
            name="Hand-added Composer",
            order=0,
        )
        m.director = ["Alice"]
        m.save()
        m.sync_credits_from_metadata()
        assert m.credits.filter(role=CreditRole.Composer).count() == 1


@pytest.mark.django_db(databases="__all__")
class TestSchemaCreditResolvers:
    """Schemas must source credit fields from ItemCredit via resolve_*.

    Regression for NEODB-SOCIAL-4MQ: resolver methods defined on a plain mixin
    (not a Schema subclass) were silently dropped by ninja's ResolverMetaclass,
    so ap_object fell back to the raw jsondata field. When that field held
    corrupted scalar data (string instead of list) Pydantic raised
    `Input should be a valid list`.
    """

    def _seed_credit(self, item: Item, role: str, name: str) -> None:
        ItemCredit.objects.create(item=item, role=role, name=name, order=0)

    def _assert_director_resolved(self, item: Item, expected: list[str]) -> None:
        # Stash a corrupted scalar in the legacy jsondata field; the resolver
        # must ignore it and read from credits instead.
        setattr(item, "director", "corrupt-string")
        item.save()
        self._seed_credit(item, CreditRole.Director, expected[0])
        # Bust the cached_property since we just inserted a credit.
        item.__dict__.pop("role_credits", None)
        assert item.ap_object["director"] == expected

    def test_tvseason_resolves_director_from_credits(self):
        season = TVSeason.objects.create(title="Season")
        season.localized_title = [{"lang": "en", "text": "Season"}]
        self._assert_director_resolved(season, ["Real Director"])

    def test_tvshow_resolves_director_from_credits(self):
        show = TVShow.objects.create(title="Show")
        show.localized_title = [{"lang": "en", "text": "Show"}]
        self._assert_director_resolved(show, ["Real Director"])

    def test_performance_resolves_director_from_credits(self):
        perf = Performance.objects.create(title="Play")
        perf.localized_title = [{"lang": "en", "text": "Play"}]
        self._assert_director_resolved(perf, ["Real Director"])

    def test_performance_production_resolves_director_from_credits(self):
        perf = Performance.objects.create(title="Play")
        perf.localized_title = [{"lang": "en", "text": "Play"}]
        perf.save()
        prod = PerformanceProduction.objects.create(title="Run", show=perf)
        prod.localized_title = [{"lang": "en", "text": "Run"}]
        self._assert_director_resolved(prod, ["Real Director"])

    def test_movie_resolves_director_from_credits(self):
        m = Movie.objects.create(title="Film")
        m.localized_title = [{"lang": "en", "text": "Film"}]
        self._assert_director_resolved(m, ["Real Director"])
