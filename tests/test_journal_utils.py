import pytest

from catalog.book.models import Edition
from journal.models.comment import Comment
from journal.models.common import Debris
from journal.models.mark import Mark
from journal.models.rating import Rating
from journal.models.shelf import ShelfLogEntry, ShelfMember, ShelfType
from journal.models.tag import TagMember
from journal.models.utils import update_journal_for_merged_item
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestUpdateJournalForMergedItem:
    """
    Tests for update_journal_for_merged_item utility, ensuring journal pieces
    (shelf entries, comments, ratings, tags) are moved from a merged legacy item
    to its new item, and duplicates can be removed.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        # create a user and two catalog items (legacy and new)
        self.user = User.register(email="test@example.com", username="testuser")
        self.identity = self.user.identity
        self.legacy = Edition.objects.create(title="Legacy Item")
        self.new = Edition.objects.create(title="New Item")
        # mark legacy as merged into new
        self.legacy.merged_to_item = self.new
        self.legacy.save()

    def test_update_journal_for_merged_item_moves_pieces(self):
        # create journal pieces on the legacy item via Mark.update
        mark = Mark(self.identity, self.legacy)
        mark.update(
            ShelfType.WISHLIST,
            comment_text="Test Comment",
            rating_grade=3,
            tags=["tag1", "tag2"],
            visibility=1,
        )
        # precondition: pieces exist on legacy
        assert ShelfMember.objects.filter(
            owner=self.identity, item=self.legacy
        ).exists()
        assert Comment.objects.filter(owner=self.identity, item=self.legacy).exists()
        assert Rating.objects.filter(owner=self.identity, item=self.legacy).exists()
        assert (
            TagMember.objects.filter(owner=self.identity, item=self.legacy).count() == 2
        )

        # perform the update
        update_journal_for_merged_item(self.legacy.uuid)

        # postcondition: no pieces remain on legacy
        assert not ShelfMember.objects.filter(
            owner=self.identity, item=self.legacy
        ).exists()
        assert not Comment.objects.filter(
            owner=self.identity, item=self.legacy
        ).exists()
        assert not Rating.objects.filter(owner=self.identity, item=self.legacy).exists()
        assert (
            TagMember.objects.filter(owner=self.identity, item=self.legacy).count() == 0
        )

        # all pieces have moved to the new item
        assert ShelfMember.objects.filter(owner=self.identity, item=self.new).exists()
        assert Comment.objects.filter(owner=self.identity, item=self.new).exists()
        assert Rating.objects.filter(owner=self.identity, item=self.new).exists()
        assert TagMember.objects.filter(owner=self.identity, item=self.new).count() == 2
        assert ShelfLogEntry.objects.filter(owner=self.identity, item=self.new).exists()

    def test_update_journal_for_merged_item_delete_duplicated(self):
        # first, create a piece on the new item to trigger duplication
        mark_new = Mark(self.identity, self.new)
        mark_new.update(
            ShelfType.WISHLIST,
            comment_text="Existing",
            rating_grade=5,
            tags=["tag1", "tag2"],
            visibility=1,
        )
        # ensure a single shelf entry exists on new
        assert (
            ShelfMember.objects.filter(owner=self.identity, item=self.new).count() == 1
        )

        # then, create a piece on the legacy item
        mark_legacy = Mark(self.identity, self.legacy)
        mark_legacy.update(
            ShelfType.WISHLIST,
            comment_text="Duplicate",
            rating_grade=4,
            tags=["tag1", "tag2"],
            visibility=1,
        )

        # perform the update with delete_duplicated=True
        update_journal_for_merged_item(self.legacy.uuid, delete_duplicated=True)

        # shelf entries for new remain unique
        assert (
            ShelfMember.objects.filter(owner=self.identity, item=self.new).count() == 1
        )
        # debris record created for the discarded duplicate
        assert Debris.objects.filter(
            owner=self.identity,
            item=self.new,
            class_name="ShelfMember",
        ).exists()
