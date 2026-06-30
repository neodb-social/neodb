import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, ExternalResource, IdType, ItemCredit, Movie
from journal.apis.collection import _prefetch_collection_member_items
from journal.models.collection import Collection
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestCollectionListOperations:
    """Test List abstract model operations via Collection (concrete subclass)."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(
            email="collection_test@test.com", username="collection_tester"
        )
        self.book1 = Edition.objects.create(title="Collection Book 1")
        self.book2 = Edition.objects.create(title="Collection Book 2")
        self.book3 = Edition.objects.create(title="Collection Book 3")
        self.movie = Movie.objects.create(title="Collection Movie")
        self.collection = Collection(
            owner=self.user.identity,
            title="Test Collection",
            brief="A test collection",
        )
        self.collection.save()

    def test_append_item(self):
        member, created = self.collection.append_item(self.book1)
        assert created is True
        assert member.item == self.book1
        assert member.position == 1

    def test_append_item_duplicate(self):
        self.collection.append_item(self.book1)
        member, created = self.collection.append_item(self.book1)
        assert created is False

    def test_append_item_none_raises(self):
        with pytest.raises(ValueError, match="item is None"):
            self.collection.append_item(None)

    def test_append_multiple_items_positions(self):
        m1, _ = self.collection.append_item(self.book1)
        m2, _ = self.collection.append_item(self.book2)
        m3, _ = self.collection.append_item(self.book3)
        assert m1.position == 1
        assert m2.position == 2
        assert m3.position == 3

    def test_remove_item(self):
        self.collection.append_item(self.book1)
        assert self.collection.members.count() == 1
        self.collection.remove_item(self.book1)
        assert self.collection.members.count() == 0

    def test_remove_item_not_in_collection(self):
        # Should not raise
        self.collection.remove_item(self.book1)

    def test_ordered_members(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.book3)
        members = list(self.collection.ordered_members)
        assert len(members) == 3
        assert members[0].item == self.book1
        assert members[1].item == self.book2
        assert members[2].item == self.book3

    def test_get_member_for_item(self):
        self.collection.append_item(self.book1)
        member = self.collection.get_member_for_item(self.book1)
        assert member is not None
        assert member.item == self.book1

    def test_get_member_for_item_not_found(self):
        member = self.collection.get_member_for_item(self.book1)
        assert member is None

    def test_move_up_item(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.book3)

        self.collection.move_up_item(self.book2)

        members = list(self.collection.ordered_members)
        assert members[0].item == self.book2
        assert members[1].item == self.book1
        assert members[2].item == self.book3

    def test_move_up_first_item_no_change(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)

        self.collection.move_up_item(self.book1)

        members = list(self.collection.ordered_members)
        assert members[0].item == self.book1
        assert members[1].item == self.book2

    def test_move_down_item(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.book3)

        self.collection.move_down_item(self.book2)

        members = list(self.collection.ordered_members)
        assert members[0].item == self.book1
        assert members[1].item == self.book3
        assert members[2].item == self.book2

    def test_move_down_last_item_no_change(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)

        self.collection.move_down_item(self.book2)

        members = list(self.collection.ordered_members)
        assert members[0].item == self.book1
        assert members[1].item == self.book2

    def test_update_member_order(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.book3)

        members = list(self.collection.ordered_members)
        original_edited_times = {m.pk: m.edited_time for m in members}
        # Reverse the order
        new_order = [members[2].pk, members[1].pk, members[0].pk]
        self.collection.update_member_order(new_order)

        reordered = list(self.collection.ordered_members)
        assert reordered[0].item == self.book3
        assert reordered[1].item == self.book2
        assert reordered[2].item == self.book1
        # bulk_update skips auto_now -- positions that moved must still bump
        # edited_time so AP "updated" timestamps stay correct.
        moved_pks = {members[0].pk, members[2].pk}
        for m in reordered:
            if m.pk in moved_pks:
                assert m.edited_time > original_edited_times[m.pk]

    def test_update_member_order_partial(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)

        members = list(self.collection.ordered_members)
        # Only include one member ID -- the other retains its original position
        self.collection.update_member_order([members[1].pk])

        m2 = self.collection.get_member_for_item(self.book2)
        # members[1] (book2) should now be at position 1
        assert m2.position == 1

    def test_update_item_metadata(self):
        self.collection.append_item(self.book1)
        self.collection.update_item_metadata(self.book1, {"note": "great"})

        member = self.collection.get_member_for_item(self.book1)
        assert member.metadata == {"note": "great"}

    def test_update_item_metadata_nonexistent(self):
        # Should not raise when item is not in collection
        self.collection.update_item_metadata(self.book1, {"note": "test"})

    def test_get_summary(self):
        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.movie)

        summary = self.collection.get_summary()
        assert summary["book"] == 2
        assert summary["movie"] == 1

    def test_get_summary_empty(self):
        summary = self.collection.get_summary()
        assert all(v == 0 for v in summary.values())

    def test_item_count_by_category(self):
        from catalog.models import ItemCategory

        self.collection.append_item(self.book1)
        self.collection.append_item(self.book2)
        self.collection.append_item(self.movie)

        counts = self.collection.item_count_by_category
        assert set(counts.keys()) == {c.value for c in ItemCategory}
        assert counts["book"] == 2
        assert counts["movie"] == 1
        assert counts["tv"] == 0

    def test_item_count_by_category_empty(self):
        from catalog.models import ItemCategory

        counts = self.collection.item_count_by_category
        assert set(counts.keys()) == {c.value for c in ItemCategory}
        assert all(v == 0 for v in counts.values())

    def test_attach_item_count_by_category_batches_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.collection.append_item(self.book1)
        self.collection.append_item(self.movie)
        other = Collection(
            owner=self.user.identity,
            title="Second Collection",
            brief="",
        )
        other.save()
        other.append_item(self.book2)

        # Fresh instances mirror the API list path (no cached_property yet).
        fresh = list(Collection.objects.filter(pk__in=[self.collection.pk, other.pk]))
        with CaptureQueriesContext(connection) as ctx:
            Collection.attach_item_count_by_category(fresh)
            # Reading the property must hit the cache populated above, not DB.
            counts_by_pk = {c.pk: c.item_count_by_category for c in fresh}
        assert len(ctx.captured_queries) == 1, ctx.captured_queries

        assert counts_by_pk[self.collection.pk]["book"] == 1
        assert counts_by_pk[self.collection.pk]["movie"] == 1
        assert counts_by_pk[other.pk]["book"] == 1
        assert counts_by_pk[other.pk]["movie"] == 0

    def test_collection_member_note(self):
        member, _ = self.collection.append_item(self.book1, note="A note about this")
        assert member.note == "A note about this"
        assert member.note_html  # should render to non-empty HTML

    def test_collection_member_note_html_empty(self):
        member, _ = self.collection.append_item(self.book1)
        assert member.note_html == ""


@pytest.mark.django_db(databases="__all__")
class TestCollectionEditItemsNPlusOne:
    """EGGPLANT-1EM: /collection/<uuid>/edit_items rendered per-item credits
    and rating distribution, firing a catalog_itemcredit join for every member.
    Credits must be batch-prefetched instead.
    """

    def test_no_per_member_credit_query(self):
        from django.db import connection
        from django.test import Client
        from django.test.utils import CaptureQueriesContext
        from django.urls import reverse

        from catalog.models import ItemCredit

        user = User.register(email="curator@test.com", username="curator")
        collection = Collection(owner=user.identity, title="C", brief="b")
        collection.save()
        movies = [Movie.objects.create(title=f"Edit Movie {i}") for i in range(4)]
        for m in movies:
            ItemCredit.objects.create(item=m, role="director", name="A Director")
            collection.append_item(m)

        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        url = reverse("journal:collection_edit_items", args=[collection.uuid])
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        assert response.status_code == 200
        credit_queries = [
            q for q in ctx.captured_queries if "catalog_itemcredit" in q["sql"]
        ]
        # Credits are batch-prefetched once for all members, not per member.
        assert len(credit_queries) <= 1, (
            f"edit_items fired {len(credit_queries)} catalog_itemcredit queries "
            f"for {len(movies)} members; expected <=1 (batched)."
        )


@pytest.mark.django_db(databases="__all__")
class TestCollectionItemsApiPrefetch:
    """The ``/collection/{uuid}/item/`` and ``/me/collection/{uuid}/item/``
    APIs serialize each member's item via ``ItemSchema``; without batch
    prefetch each item fired a per-row ``catalog_externalresource`` query.
    ``CollectionItemPageNumberPagination`` hydrates the page post-slice
    (mirrors the shelf API).
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="colpf@test.com", username="colpf")
        self.collection = Collection(
            owner=self.user.identity, title="Pf Collection", brief=""
        )
        self.collection.save()
        for i in range(3):
            book = Edition.objects.create(title=f"Pf Book {i}")
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.RSS,
                id_value=f"colpf-{i}",
                url=f"https://example.com/colpf-{i}",
            )
            ItemCredit.objects.create(item=book, role="author", name=f"Author {i}")
            self.collection.append_item(book)

    def test_member_items_external_resources_and_credits_prefetched(self):
        # Fresh member instances, as the paginator receives them post-slice.
        members = list(self.collection.ordered_members)
        _prefetch_collection_member_items(members)
        # Reading external_resources and credits (as ItemSchema does) must now
        # be served from the prefetch cache without per-item queries.
        with CaptureQueriesContext(connection) as ctx:
            for m in members:
                for res in m.item.external_resources.all():
                    _ = res.url
                list(m.item.credits.all())
        extres = [
            q
            for q in ctx.captured_queries
            if 'FROM "catalog_externalresource"' in q["sql"]
        ]
        credits = [
            q for q in ctx.captured_queries if 'FROM "catalog_itemcredit"' in q["sql"]
        ]
        assert extres == [], (
            f"reading collection member items' external_resources fired "
            f"{len(extres)} query(ies); expected 0 (prefetched). First: "
            f"{extres[0]['sql'] if extres else 'n/a'}"
        )
        assert credits == [], (
            f"reading collection member items' credits fired {len(credits)} "
            f"query(ies); expected 0 (prefetched). First: "
            f"{credits[0]['sql'] if credits else 'n/a'}"
        )
