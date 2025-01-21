import time

from django.test import TestCase

from catalog.models import *
from journal.models.common import Debris
from users.models import User

from .models import *


class CollectionTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.user = User.register(email="a@b.com", username="user")

    def test_collection(self):
        Collection.objects.create(title="test", owner=self.user.identity)
        collection = Collection.objects.get(title="test", owner=self.user.identity)
        self.assertEqual(collection.catalog_item.title, "test")
        member1, _ = collection.append_item(self.book1)
        self.assertIsNotNone(member1)
        member1.note = "my notes"
        member1.save()
        collection.append_item(self.book2, note="test")
        self.assertEqual(list(collection.ordered_items), [self.book1, self.book2])
        collection.move_up_item(self.book1)
        self.assertEqual(list(collection.ordered_items), [self.book1, self.book2])
        collection.move_up_item(self.book2)
        self.assertEqual(list(collection.ordered_items), [self.book2, self.book1])
        members = collection.ordered_members
        collection.update_member_order([members[1].id, members[0].id])
        self.assertEqual(list(collection.ordered_items), [self.book1, self.book2])
        member1 = collection.get_member_for_item(self.book1)
        self.assertIsNotNone(member1)
        if member1 is None:
            return
        self.assertEqual(member1.note, "my notes")
        member2 = collection.get_member_for_item(self.book2)
        self.assertIsNotNone(member2)
        if member2 is None:
            return
        self.assertEqual(member2.note, "test")


class ShelfTest(TestCase):
    databases = "__all__"

    def setUp(self):
        pass

    def test_shelf(self):
        user = User.register(email="a@b.com", username="user")
        shelf_manager = user.identity.shelf_manager
        self.assertEqual(len(shelf_manager.shelf_list.items()), 4)
        book1 = Edition.objects.create(title="Hyperion")
        book2 = Edition.objects.create(title="Andymion")
        q1 = shelf_manager.get_shelf(ShelfType.WISHLIST)
        q2 = shelf_manager.get_shelf(ShelfType.PROGRESS)
        self.assertIsNotNone(q1)
        self.assertIsNotNone(q2)
        self.assertEqual(q1.members.all().count(), 0)
        self.assertEqual(q2.members.all().count(), 0)
        Mark(user.identity, book1).update(ShelfType.WISHLIST)
        time.sleep(0.001)  # add a little delay to make sure the timestamp is different
        Mark(user.identity, book2).update(ShelfType.WISHLIST)
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 2)
        Mark(user.identity, book1).update(ShelfType.PROGRESS)
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 1)
        self.assertEqual(q2.members.all().count(), 1)
        self.assertEqual(len(Mark(user.identity, book1).all_post_ids), 2)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 2)
        Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 1})
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 1)
        self.assertEqual(q2.members.all().count(), 1)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 2)
        self.assertEqual(len(Mark(user.identity, book1).all_post_ids), 2)

        # theses tests are not relevant anymore, bc we don't use log to track metadata changes
        # last_log = log.last()
        # self.assertEqual(last_log.metadata if last_log else 42, {"progress": 1})
        # Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 1})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # self.assertEqual(log.count(), 3)
        # last_log = log.last()
        # self.assertEqual(last_log.metadata if last_log else 42, {"progress": 1})
        # Mark(user.identity, book1).update(ShelfType.PROGRESS, metadata={"progress": 10})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # self.assertEqual(log.count(), 4)
        # last_log = log.last()
        # self.assertEqual(last_log.metadata if last_log else 42, {"progress": 10})
        # shelf_manager.move_item(book1, ShelfType.PROGRESS)
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # self.assertEqual(log.count(), 4)
        # last_log = log.last()
        # self.assertEqual(last_log.metadata if last_log else 42, {"progress": 10})
        # shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 90})
        # time.sleep(0.001)
        # log = shelf_manager.get_log_for_item(book1)
        # self.assertEqual(log.count(), 5)

        self.assertEqual(Mark(user.identity, book1).visibility, 0)
        self.assertEqual(len(Mark(user.identity, book1).current_post_ids), 1)
        Mark(user.identity, book1).update(
            ShelfType.PROGRESS, metadata={"progress": 90}, visibility=1
        )
        self.assertEqual(len(Mark(user.identity, book1).current_post_ids), 2)
        self.assertEqual(len(Mark(user.identity, book1).all_post_ids), 3)
        time.sleep(0.001)
        Mark(user.identity, book1).update(
            ShelfType.COMPLETE, metadata={"progress": 100}, tags=["best"]
        )
        self.assertEqual(Mark(user.identity, book1).visibility, 1)
        self.assertEqual(shelf_manager.get_log_for_item(book1).count(), 3)
        self.assertEqual(len(Mark(user.identity, book1).all_post_ids), 4)

        # test delete mark ->  one more log
        Mark(user.identity, book1).delete()
        self.assertEqual(log.count(), 4)
        deleted_mark = Mark(user.identity, book1)
        self.assertEqual(deleted_mark.shelf_type, None)
        self.assertEqual(deleted_mark.tags, [])


class TagTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.movie1 = Edition.objects.create(title="Fight Club")
        self.user1 = User.register(email="a@b.com", username="user")
        self.user2 = User.register(email="x@b.com", username="user2")
        self.user3 = User.register(email="y@b.com", username="user3")

    def test_cleanup(self):
        self.assertEqual(Tag.cleanup_title("# "), "_")
        self.assertEqual(Tag.deep_cleanup_title("# C "), "c")

    def test_user_tag(self):
        t1 = "tag 1"
        t2 = "tag 2"
        t3 = "tag 3"
        TagManager.tag_item_for_owner(self.user2.identity, self.book1, [t1, t3])
        self.assertEqual(self.book1.tags, [t1, t3])
        TagManager.tag_item_for_owner(self.user2.identity, self.book1, [t2, t3])
        self.assertEqual(self.book1.tags, [t2, t3])
        m = Mark(self.user2.identity, self.book1)
        self.assertEqual(m.tags, [t2, t3])


class MarkTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.user1 = User.register(email="a@b.com", username="user")
        pref = self.user1.preference
        pref.default_visibility = 2
        pref.save()

    def test_mark(self):
        mark = Mark(self.user1.identity, self.book1)
        self.assertEqual(mark.shelf_type, None)
        self.assertEqual(mark.shelf_label, None)
        self.assertEqual(mark.comment_text, None)
        self.assertEqual(mark.rating_grade, None)
        self.assertEqual(mark.visibility, 2)
        self.assertEqual(mark.review, None)
        self.assertEqual(mark.tags, [])
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, None, 1)

        mark = Mark(self.user1.identity, self.book1)
        self.assertEqual(mark.shelf_type, ShelfType.WISHLIST)
        self.assertEqual(mark.shelf_label, "books to read")
        self.assertEqual(mark.comment_text, "a gentle comment")
        self.assertEqual(mark.rating_grade, 9)
        self.assertEqual(mark.visibility, 1)
        self.assertEqual(mark.review, None)
        self.assertEqual(mark.tags, [])

    def test_review(self):
        review = Review.update_item_review(
            self.book1, self.user1.identity, "Critic", "Review"
        )
        mark = Mark(self.user1.identity, self.book1)
        self.assertEqual(mark.review, review)
        Review.update_item_review(self.book1, self.user1.identity, None, None)
        mark = Mark(self.user1.identity, self.book1)
        self.assertIsNone(mark.review)

    def test_tag(self):
        TagManager.tag_item_for_owner(
            self.user1.identity, self.book1, [" Sci-Fi ", " fic "]
        )
        mark = Mark(self.user1.identity, self.book1)
        self.assertEqual(mark.tags, ["Sci-Fi", "fic"])


class DebrisTest(TestCase):
    databases = "__all__"

    def setUp(self):
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
        self.assertEqual(cnt, 0)

        mark = Mark(self.user1.identity, self.book3)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi", "fic"], 1)
        Review.update_item_review(self.book3, self.user1.identity, "Critic", "Review")
        collection.append_item(self.book3)
        self.book3.merge_to(self.book2)
        update_journal_for_merged_item(self.book3.uuid, delete_duplicated=True)
        cnt = Debris.objects.all().count()
        self.assertEqual(cnt, 4)  # Rating, Shelf, 2x TagMember


class NoteTest(TestCase):
    databases = "__all__"

    # def setUp(self):
    #     self.book1 = Edition.objects.create(title="Hyperion")
    #     self.user1 = User.register(email="test@test", username="test")

    def test_parse(self):
        c0 = "test \n - \n"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, c0)
        self.assertEqual(t, None)
        self.assertEqual(v, None)

        c0 = "test\n \n - \nhttps://xyz"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test\n ")
        self.assertEqual(t, None)
        self.assertEqual(v, None)

        c0 = "test \n - \np1"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.PAGE)
        self.assertEqual(v, "1")

        c0 = "test \n - \nP 99"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.PAGE)
        self.assertEqual(v, "99")

        c0 = "test \n - \n pt 1 "
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.PART)
        self.assertEqual(v, "1")

        c0 = "test \n - \nx chapter 1.1 \n"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.CHAPTER)
        self.assertEqual(v, "1.1")

        c0 = "test \n - \n book pg 1.1% "
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.PERCENTAGE)
        self.assertEqual(v, "1.1")

        c0 = "test \n - \n show e 1. "
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.EPISODE)
        self.assertEqual(v, "1.")

        c0 = "test \n - \nch 2"
        c, t, v = Note.strip_footer(c0)
        self.assertEqual(c, "test ")
        self.assertEqual(t, Note.ProgressType.CHAPTER)
        self.assertEqual(v, "2")
