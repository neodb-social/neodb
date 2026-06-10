import pytest

from catalog.models import Edition
from journal.models import (
    Collection,
    CollectionMember,
    Comment,
    Mark,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfType,
    Tag,
    TagMember,
)
from journal.models.utils import (
    journal_exists_for_item,
    remove_data_by_identity,
    reset_journal_visibility_for_user,
)
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestResetJournalVisibility:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="vis@test.com", username="vis_user")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Test Book")
        Mark(self.identity, self.book).update(
            ShelfType.WISHLIST, "comment", 5, ["tag"], 0
        )
        Review.update_item_review(self.book, self.identity, "Title", "Body")

    def test_reset_visibility_updates_all_pieces(self):
        assert ShelfMember.objects.filter(owner=self.identity, visibility=0).exists()
        assert Comment.objects.filter(owner=self.identity, visibility=0).exists()
        assert Rating.objects.filter(owner=self.identity, visibility=0).exists()
        assert Review.objects.filter(owner=self.identity, visibility=0).exists()
        reset_journal_visibility_for_user(self.identity, 2)
        assert ShelfMember.objects.filter(owner=self.identity, visibility=2).exists()
        assert Comment.objects.filter(owner=self.identity, visibility=2).exists()
        assert Rating.objects.filter(owner=self.identity, visibility=2).exists()
        assert Review.objects.filter(owner=self.identity, visibility=2).exists()
        assert not ShelfMember.objects.filter(
            owner=self.identity, visibility=0
        ).exists()

    def test_reset_visibility_to_public(self):
        reset_journal_visibility_for_user(self.identity, 2)
        reset_journal_visibility_for_user(self.identity, 0)
        assert ShelfMember.objects.filter(owner=self.identity, visibility=0).exists()


@pytest.mark.django_db(databases="__all__")
class TestRemoveDataByIdentity:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="rm@test.com", username="rm_user")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Remove Book")

    def test_remove_data_clears_all_journal_entries(self):
        Mark(self.identity, self.book).update(
            ShelfType.COMPLETE, "done", 10, ["best"], 0
        )
        Review.update_item_review(self.book, self.identity, "Title", "Body")
        collection = Collection.objects.create(title="col", owner=self.identity)
        collection.append_item(self.book)
        Note.objects.create(
            owner=self.identity, item=self.book, content="note", visibility=0
        )
        assert ShelfMember.objects.filter(owner=self.identity).exists()
        assert Comment.objects.filter(owner=self.identity).exists()
        assert Rating.objects.filter(owner=self.identity).exists()
        assert Review.objects.filter(owner=self.identity).exists()
        assert TagMember.objects.filter(owner=self.identity).exists()
        assert Note.objects.filter(owner=self.identity).exists()
        assert CollectionMember.objects.filter(owner=self.identity).exists()
        remove_data_by_identity(self.identity)
        assert not ShelfMember.objects.filter(owner=self.identity).exists()
        assert not ShelfLogEntry.objects.filter(owner=self.identity).exists()
        assert not Comment.objects.filter(owner=self.identity).exists()
        assert not Rating.objects.filter(owner=self.identity).exists()
        assert not Review.objects.filter(owner=self.identity).exists()
        assert not TagMember.objects.filter(owner=self.identity).exists()
        assert not Tag.objects.filter(owner=self.identity).exists()
        assert not Note.objects.filter(owner=self.identity).exists()
        assert not CollectionMember.objects.filter(owner=self.identity).exists()
        assert not Collection.objects.filter(owner=self.identity).exists()


@pytest.mark.django_db(databases="__all__")
class TestJournalExistsForItem:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="je@test.com", username="je_user")
        self.identity = self.user.identity
        self.book1 = Edition.objects.create(title="Has Journal")
        self.book2 = Edition.objects.create(title="No Journal")

    def test_returns_true_when_content_exists(self):
        Mark(self.identity, self.book1).update(ShelfType.WISHLIST)
        assert journal_exists_for_item(self.book1) is True

    def test_returns_false_when_no_content(self):
        assert journal_exists_for_item(self.book2) is False

    def test_returns_true_with_collection_member(self):
        collection = Collection.objects.create(title="col", owner=self.identity)
        collection.append_item(self.book2)
        assert journal_exists_for_item(self.book2) is True
