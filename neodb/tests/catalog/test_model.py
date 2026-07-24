import pytest
from django.db import connection
from django.db.models import prefetch_related_objects
from django.test.utils import CaptureQueriesContext
from django.utils import translation

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

    def test_edition_resolves_publisher_from_credits(self):
        edition = Edition.objects.create(title="Book")
        edition.localized_title = [{"lang": "en", "text": "Book"}]
        edition.publisher = ["stale-jsondata"]
        edition.save()
        ItemCredit.objects.create(
            item=edition, role=CreditRole.Publisher, name="Penguin", order=0
        )
        edition.__dict__.pop("role_credits", None)
        ap = edition.ap_object
        assert ap["publisher"] == ["Penguin"]
        # Deprecated scalar still exposed for API back-compat.
        assert ap["pub_house"] == "Penguin"


@pytest.mark.django_db(databases="__all__")
class TestCreditDisplayNameLocalization:
    """ItemCredit.display_name shows a request-localized credit name for HTML
    display once Item.attach_localized_credit_names has run, instead of the
    snapshot frozen at sync time. attach_localized_credit_names fetches only the
    localized_name JSON sub-key in one bounded query -- never the heavy person
    metadata blob (EGGPLANT-1EF) and never a per-credit query. Canonical
    surfaces (credit_names_by_role, which feeds ap_object / backups / schema.org
    / import matching) always keep the snapshot.

    Regression: a Douban-sourced movie froze the director credit name in
    Chinese, so the movie page rendered Chinese even for English viewers, while
    the person page (which localizes live) showed English.
    """

    def _linked_movie(self, n: int = 1) -> Movie:
        m = Movie.objects.create(title="Test Film")
        m.localized_title = [{"lang": "en", "text": "Test Film"}]
        m.save()
        for i in range(n):
            person = People.objects.create(people_type="person", title=f"del Toro {i}")
            person.localized_name = [
                {"lang": "zh-cn", "text": "吉尔莫·德尔·托罗"},
                {"lang": "en", "text": "Guillermo del Toro"},
            ]
            person.save()
            # Frozen snapshot in Chinese, as captured at sync time.
            ItemCredit.objects.create(
                item=m,
                role=CreditRole.Director,
                name="吉尔莫·德尔·托罗",
                person=person,
                order=i,
            )
        return m

    def _localized_director(self, movie: Movie) -> ItemCredit:
        # Fresh objects per language (People localization is per-request).
        m = Movie.objects.get(pk=movie.pk)
        Item.attach_localized_credit_names([m])
        return m.role_credits[CreditRole.Director][0]

    def test_attach_localizes_linked_credit_by_language(self):
        m = self._linked_movie()
        with translation.override("en"):
            assert self._localized_director(m).display_name == "Guillermo del Toro"
        with translation.override("zh-hans"):
            assert self._localized_director(m).display_name == "吉尔莫·德尔·托罗"

    def test_unlinked_credit_uses_frozen_name(self):
        m = Movie.objects.create(title="Test Film")
        m.localized_title = [{"lang": "en", "text": "Test Film"}]
        m.save()
        ItemCredit.objects.create(
            item=m, role=CreditRole.Director, name="吉尔莫·德尔·托罗", order=0
        )
        with translation.override("en"):
            assert self._localized_director(m).display_name == "吉尔莫·德尔·托罗"

    def test_without_attach_display_name_is_frozen_and_issues_no_query(self):
        # Surfaces that don't call attach (cards/lists/API) keep the snapshot,
        # and display_name must never trigger a query on its own.
        base = self._linked_movie()
        with translation.override("en"):
            m = Movie.objects.get(pk=base.pk)
            prefetch_related_objects([m], Item.credits_prefetch())
            credit = m.role_credits[CreditRole.Director][0]
            with CaptureQueriesContext(connection) as ctx:
                assert credit.display_name == "吉尔莫·德尔·托罗"
            assert len(ctx) == 0

    def test_credit_names_by_role_stays_canonical(self):
        # ap_object / backups / schema.org / import matching must not localize.
        base = self._linked_movie()
        for lang in ("en", "zh-hans"):
            with translation.override(lang):
                m = Movie.objects.get(pk=base.pk)
                assert m.credit_names_by_role("director") == ["吉尔莫·德尔·托罗"]

    def test_attach_uses_single_query_regardless_of_cast_size(self):
        # One bounded query for all credited people -- flat, not an N+1, and it
        # must not pull the heavy person metadata column.
        base = self._linked_movie(n=12)
        with translation.override("en"):
            m = Movie.objects.get(pk=base.pk)
            _ = m.role_credits  # warm the credits load out of the measured block
            with CaptureQueriesContext(connection) as ctx:
                Item.attach_localized_credit_names([m])
            assert len(ctx) == 1
            assert m.role_credits[CreditRole.Director][0].display_name == (
                "Guillermo del Toro"
            )


@pytest.mark.django_db(databases="__all__")
class TestEditionLegacyMetadataCoercion:
    """Editions ingested from older peers / pre-migration backups may carry
    legacy ``pub_house`` (str) keys or list-shaped ``imprint`` keys (from
    the short-lived intermediate state). ``normalize_legacy_metadata``
    rewrites them to the current shape, including the ``"['Foo']"``
    literal-string corruption left by the prior form round-trip bug.
    """

    def test_normalizes_pub_house_string(self):
        m = {"pub_house": "Penguin"}
        Edition.normalize_legacy_metadata(m)
        assert m == {"publisher": ["Penguin"]}

    def test_normalizes_pub_house_list(self):
        m = {"pub_house": ["Penguin", "Random House"]}
        Edition.normalize_legacy_metadata(m)
        assert m == {"publisher": ["Penguin", "Random House"]}

    def test_unwraps_corrupted_pub_house_list_repr(self):
        m = {"pub_house": "['Penguin']"}
        Edition.normalize_legacy_metadata(m)
        assert m == {"publisher": ["Penguin"]}

    def test_collapses_list_imprint_to_string(self):
        m = {"imprint": ["Vintage"]}
        Edition.normalize_legacy_metadata(m)
        assert m == {"imprint": "Vintage"}

    def test_collapses_multi_imprint_list_with_slash(self):
        m = {"imprint": ["Vintage", "Anchor"]}
        Edition.normalize_legacy_metadata(m)
        assert m == {"imprint": "Vintage/Anchor"}

    def test_unwraps_corrupted_imprint_list_repr(self):
        m = {"imprint": "['Vintage']"}
        Edition.normalize_legacy_metadata(m)
        assert m == {"imprint": "Vintage"}

    def test_keeps_scalar_imprint(self):
        m = {"imprint": "Vintage"}
        Edition.normalize_legacy_metadata(m)
        assert m == {"imprint": "Vintage"}

    def test_drops_empty_imprint_list(self):
        m = {"imprint": []}
        Edition.normalize_legacy_metadata(m)
        assert "imprint" not in m

    def test_existing_publisher_list_takes_precedence(self):
        # New shape already present; legacy pub_house is dropped, not merged.
        m = {"publisher": ["Penguin"], "pub_house": "Old Stale"}
        Edition.normalize_legacy_metadata(m)
        assert m == {"publisher": ["Penguin"]}

    def test_empty_pub_house_drops_key(self):
        m = {"pub_house": "", "publisher": []}
        Edition.normalize_legacy_metadata(m)
        # Empty stays empty, pub_house dropped.
        assert "pub_house" not in m
        assert m.get("publisher", []) == []
