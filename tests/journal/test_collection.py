import pytest

from catalog.models import Edition, Movie
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
        # Reverse the order
        new_order = [members[2].pk, members[1].pk, members[0].pk]
        self.collection.update_member_order(new_order)

        reordered = list(self.collection.ordered_members)
        assert reordered[0].item == self.book3
        assert reordered[1].item == self.book2
        assert reordered[2].item == self.book1

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

    def test_collection_member_note(self):
        member, _ = self.collection.append_item(self.book1, note="A note about this")
        assert member.note == "A note about this"
        assert member.note_html  # should render to non-empty HTML

    def test_collection_member_note_html_empty(self):
        member, _ = self.collection.append_item(self.book1)
        assert member.note_html == ""
