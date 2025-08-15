import time

import pytest

from catalog.models import Edition
from journal.models import *
from journal.models.common import Debris
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestCollection:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.user = User.register(email="a@b.com", username="user")

    def test_collection(self):
        Collection.objects.create(title="test", owner=self.user.identity)
        collection = Collection.objects.get(title="test", owner=self.user.identity)
        assert collection.catalog_item.title == "test"
        member1, _ = collection.append_item(self.book1)
        assert member1 is not None
        member1.note = "my notes"  # type: ignore
        member1.save()
        collection.append_item(self.book2, note="test")
        assert list(collection.ordered_items) == [self.book1, self.book2]
        collection.move_up_item(self.book1)
        assert list(collection.ordered_items) == [self.book1, self.book2]
        collection.move_up_item(self.book2)
        assert list(collection.ordered_items) == [self.book2, self.book1]
        members = collection.ordered_members
        collection.update_member_order([members[1].pk, members[0].pk])
        assert list(collection.ordered_items) == [self.book1, self.book2]
        member1 = collection.get_member_for_item(self.book1)
        assert member1 is not None
        if member1 is None:
            return
        assert member1.note == "my notes"  # type: ignore
        member2 = collection.get_member_for_item(self.book2)
        assert member2 is not None
        if member2 is None:
            return
        assert member2.note == "test"  # type: ignore


@pytest.mark.django_db(databases="__all__")
class TestShelf:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        pass

    def test_shelf(self):
        user = User.register(email="a@b.com", username="user")
        shelf_manager = user.identity.shelf_manager
        assert len(shelf_manager.shelf_list.items()) == 4
        book1 = Edition.objects.create(title="Hyperion")
        book2 = Edition.objects.create(title="Andymion")
        q1 = shelf_manager.get_shelf(ShelfType.WISHLIST)
        q2 = shelf_manager.get_shelf(ShelfType.PROGRESS)
        assert q1 is not None
        assert q2 is not None
        assert q1.members.all().count() == 0
        assert q2.members.all().count() == 0
        Mark(user.identity, book1).update(ShelfType.WISHLIST)
        Mark(user.identity, book2).update(ShelfType.WISHLIST)
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book1)]
        assert log == ["wishlist"]
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book2)]
        assert log == ["wishlist"]
        time.sleep(0.001)  # add a little delay to make sure the timestamp is different

        Mark(user.identity, book1).update(ShelfType.WISHLIST)
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book1)]
        assert log == ["wishlist"]
        time.sleep(0.001)

        assert q1.members.all().count() == 2
        Mark(user.identity, book1).update(ShelfType.PROGRESS)
        assert q1.members.all().count() == 1
        assert q2.members.all().count() == 1
        time.sleep(0.001)

        assert len(Mark(user.identity, book1).all_post_ids) == 2
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book1)]

        assert log == ["wishlist", "progress"]
        Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 1})
        time.sleep(0.001)
        assert q1.members.all().count() == 1
        assert q2.members.all().count() == 1
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book1)]
        assert log == ["wishlist", "progress"]
        assert len(Mark(user.identity, book1).all_post_ids) == 2

        # theses tests are not relevant anymore, bc we don't use log to track metadata changes
        # last_log = log.last()
        # assert last_log.metadata if last_log else 42 == {"progress": 1}
        # Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 1})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # assert log.count() == 3
        # last_log = log.last()
        # assert last_log.metadata if last_log else 42 == {"progress": 1}
        # Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 10})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # assert log.count() == 4
        # last_log = log.last()
        # assert last_log.metadata if last_log else 42 == {"progress": 10}
        # shelf_manager.move_item(book1, ShelfType.PROGRESS)
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # assert log.count() == 4
        # last_log = log.last()
        # assert last_log.metadata if last_log else 42 == {"progress": 10}
        # shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 90})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # assert log.count() == 5

        assert Mark(user.identity, book1).visibility == 0
        assert len(Mark(user.identity, book1).current_post_ids) == 1
        Mark(user.identity, book1).update(
            ShelfType.PROGRESS, metadata={"progress": 90}, visibility=1
        )
        assert len(Mark(user.identity, book1).current_post_ids) == 2
        assert len(Mark(user.identity, book1).all_post_ids) == 3
        time.sleep(0.001)
        Mark(user.identity, book1).update(
            ShelfType.COMPLETE, metadata={"progress": 100}, tags=["best"]
        )
        assert Mark(user.identity, book1).visibility == 1
        assert shelf_manager.get_log_for_item(book1).count() == 3
        assert len(Mark(user.identity, book1).all_post_ids) == 4

        # test delete mark ->  one more log
        Mark(user.identity, book1).delete()
        log = [ll.shelf_type for ll in shelf_manager.get_log_for_item(book1)]
        assert log == ["wishlist", "progress", "complete", None]
        deleted_mark = Mark(user.identity, book1)
        assert deleted_mark.shelf_type is None
        assert deleted_mark.tags == []


@pytest.mark.django_db(databases="__all__")
class TestTag:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.movie1 = Edition.objects.create(title="Fight Club")
        self.user1 = User.register(email="a@b.com", username="user")
        self.user2 = User.register(email="x@b.com", username="user2")
        self.user3 = User.register(email="y@b.com", username="user3")

    def test_cleanup(self):
        assert Tag.cleanup_title("# ") == "_"
        assert Tag.deep_cleanup_title("# C ") == "c"

    def test_user_tag(self):
        t1 = "tag 1"
        t2 = "tag 2"
        t3 = "tag 3"
        TagManager.tag_item_for_owner(self.user2.identity, self.book1, [t1, t3])
        # self.book1.tags is precached when self.book1 was created (and indexed)
        assert TagManager.indexable_tags_for_item(self.book1) == [t1, t3]
        TagManager.tag_item_for_owner(self.user2.identity, self.book1, [t2, t3])
        assert TagManager.indexable_tags_for_item(self.book1) == [t2, t3]
        m = Mark(self.user2.identity, self.book1)
        assert m.tags == [t2, t3]


@pytest.mark.django_db(databases="__all__")
class TestMark:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Endymion")
        self.book3 = Edition.objects.create(title="Fall of Hyperion")
        self.user1 = User.register(email="a@b.com", username="user")
        pref = self.user1.preference
        pref.default_visibility = 2
        pref.save()

    def test_mark(self):
        mark = Mark(self.user1.identity, self.book1)
        assert mark.shelf_type is None
        assert mark.shelf_label is None
        assert mark.comment_text is None
        assert mark.rating_grade is None
        assert mark.visibility == 2
        assert mark.review is None
        assert mark.tags == []
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, None, 1)

        mark = Mark(self.user1.identity, self.book1)
        assert mark.shelf_type == ShelfType.WISHLIST
        assert mark.shelf_label == "books to read"
        assert mark.comment_text == "a gentle comment"
        assert mark.rating_grade == 9
        assert mark.visibility == 1
        assert mark.review is None
        assert mark.tags == []

    def test_review(self):
        review = Review.update_item_review(
            self.book1, self.user1.identity, "Critic", "Review"
        )
        mark = Mark(self.user1.identity, self.book1)
        assert mark.review == review
        Review.update_item_review(self.book1, self.user1.identity, None, None)
        mark = Mark(self.user1.identity, self.book1)
        assert mark.review is None

    def test_tag(self):
        TagManager.tag_item_for_owner(
            self.user1.identity, self.book1, [" Sci-Fi ", " fic "]
        )
        mark = Mark(self.user1.identity, self.book1)
        assert mark.tags == ["Sci-Fi", "fic"]

    def test_attach_to_items(self):
        # Create different marks for each book
        mark1 = Mark(self.user1.identity, self.book1)
        mark1.update(ShelfType.WISHLIST, "wishlist comment", 8, ["sci-fi", "book"], 1)

        mark2 = Mark(self.user1.identity, self.book2)
        mark2.update(ShelfType.PROGRESS, "progress comment", 9, ["fantasy"], 2)

        review = Review.update_item_review(
            self.book3, self.user1.identity, "Critic", "Review Content"
        )
        mark3 = Mark(self.user1.identity, self.book3)
        mark3.update(ShelfType.COMPLETE, "complete comment", 10, ["space-opera"], 0)

        # Call attach_to_items on all books
        items = [self.book1, self.book2, self.book3]
        Mark.attach_to_items(self.user1.identity, items, self.user1)

        # Verify each item has the correct mark attributes
        for item in items:
            # Each item should have the mark property with the correct attributes
            assert hasattr(item, "mark")

            if item == self.book1:
                assert item.mark.shelf_type == ShelfType.WISHLIST
                assert item.mark.comment_text == "wishlist comment"
                assert item.mark.rating_grade == 8
                assert sorted(item.mark.tags) == sorted(["sci-fi", "book"])
                assert item.mark.visibility == 1
                assert item.mark.review is None

            elif item == self.book2:
                assert item.mark.shelf_type == ShelfType.PROGRESS
                assert item.mark.comment_text == "progress comment"
                assert item.mark.rating_grade == 9
                assert item.mark.tags == ["fantasy"]
                assert item.mark.visibility == 2
                assert item.mark.review is None

            elif item == self.book3:
                assert item.mark.shelf_type == ShelfType.COMPLETE
                assert item.mark.comment_text == "complete comment"
                assert item.mark.rating_grade == 10
                assert item.mark.tags == ["space-opera"]
                assert item.mark.visibility == 0
                assert item.mark.review == review

        Mark.attach_to_items(self.user1.identity, items, None)

        # Verify each item has the correct mark attributes
        for item in items:
            # Each item should have the mark property with the correct attributes
            assert hasattr(item, "mark")

            if item == self.book1:
                assert item.mark.shelf_type is None
                assert sorted(item.mark.tags) == sorted([])

            elif item == self.book2:
                assert item.mark.shelf_type is None

            elif item == self.book3:
                assert item.mark.shelf_type == ShelfType.COMPLETE
                assert item.mark.comment_text == "complete comment"
                assert item.mark.rating_grade == 10
                assert item.mark.tags == ["space-opera"]
                assert item.mark.visibility == 0
                assert item.mark.review == review


@pytest.mark.django_db(databases="__all__")
class TestDebris:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Hyperion clone")
        self.book3 = Edition.objects.create(title="Hyperion clone 2")
        self.user1 = User.register(email="test@test", username="test")

    def test_journal_migration(self):
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi", "fic"], 1)
        Review.update_item_review(self.book1, self.user1.identity, "Critic", "Review")
        collection = Collection.objects.create(title="test", owner=self.user1.identity)
        collection.append_item(self.book1)
        self.book1.merge_to(self.book2)
        update_journal_for_merged_item(self.book1.uuid, delete_duplicated=True)
        cnt = Debris.objects.all().count()
        assert cnt == 0

        mark = Mark(self.user1.identity, self.book3)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi", "fic"], 1)
        Review.update_item_review(self.book3, self.user1.identity, "Critic", "Review")
        collection.append_item(self.book3)
        self.book3.merge_to(self.book2)
        update_journal_for_merged_item(self.book3.uuid, delete_duplicated=True)
        cnt = Debris.objects.all().count()
        assert cnt == 4  # Rating, Shelf, 2x TagMember


@pytest.mark.django_db(databases="__all__")
class TestNote:
    # @pytest.fixture(autouse=True)
    # def setup_data(self):
    #     self.book1 = Edition.objects.create(title="Hyperion")
    #     self.user1 = User.register(email="test@test", username="test")

    def test_parse(self):
        c0 = "test \n - \n"
        c, t, v = Note.strip_footer(c0)
        assert c == c0
        assert t is None
        assert v is None

        c0 = "test\n \n - \nhttps://xyz"
        c, t, v = Note.strip_footer(c0)
        assert c == "test\n "
        assert t is None
        assert v is None

        c0 = "test \n - \np1"
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.PAGE
        assert v == "1"

        c0 = "test \n - \nP 99"
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.PAGE
        assert v == "99"

        c0 = "test \n - \n pt 1 "
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.PART
        assert v == "1"

        c0 = "test \n - \nx chapter 1.1 \n"
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.CHAPTER
        assert v == "1.1"

        c0 = "test \n - \n book pg 1.1% "
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.PERCENTAGE
        assert v == "1.1"

        c0 = "test \n - \n show e 1. "
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.EPISODE
        assert v == "1."

        c0 = "test \n - \nch 2"
        c, t, v = Note.strip_footer(c0)
        assert c == "test "
        assert t == Note.ProgressType.CHAPTER
        assert v == "2"
