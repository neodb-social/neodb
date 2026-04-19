import pytest

from catalog.models import CreditRole, Edition, Item, ItemCredit, Movie
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
