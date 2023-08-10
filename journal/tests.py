import time

from django.test import TestCase

from catalog.models import *
from users.models import User

from .models import *


class CollectionTest(TestCase):
    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.user = User.register(email="a@b.com")
        pass

    def test_collection(self):
        collection = Collection.objects.create(title="test", owner=self.user)
        collection = Collection.objects.filter(title="test", owner=self.user).first()
        self.assertEqual(collection.catalog_item.title, "test")
        member1 = collection.append_item(self.book1)
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
        self.assertEqual(member1.note, "my notes")
        member2 = collection.get_member_for_item(self.book2)
        self.assertEqual(member2.note, "test")


class ShelfTest(TestCase):
    def setUp(self):
        pass

    def test_shelf(self):
        user = User.register(mastodon_site="site", mastodon_username="name")
        shelf_manager = ShelfManager(user=user)
        self.assertEqual(user.shelf_set.all().count(), 3)
        book1 = Edition.objects.create(title="Hyperion")
        book2 = Edition.objects.create(title="Andymion")
        q1 = shelf_manager.get_shelf(ShelfType.WISHLIST)
        q2 = shelf_manager.get_shelf(ShelfType.PROGRESS)
        self.assertIsNotNone(q1)
        self.assertIsNotNone(q2)
        self.assertEqual(q1.members.all().count(), 0)
        self.assertEqual(q2.members.all().count(), 0)
        shelf_manager.move_item(book1, ShelfType.WISHLIST)
        time.sleep(0.001)
        shelf_manager.move_item(book2, ShelfType.WISHLIST)
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 2)
        shelf_manager.move_item(book1, ShelfType.PROGRESS)
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 1)
        self.assertEqual(q2.members.all().count(), 1)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 2)
        self.assertEqual(log.last().metadata, {})
        shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 1})
        time.sleep(0.001)
        self.assertEqual(q1.members.all().count(), 1)
        self.assertEqual(q2.members.all().count(), 1)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 3)
        self.assertEqual(log.last().metadata, {"progress": 1})
        shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 1})
        time.sleep(0.001)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 3)
        self.assertEqual(log.last().metadata, {"progress": 1})
        shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 10})
        time.sleep(0.001)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 4)
        self.assertEqual(log.last().metadata, {"progress": 10})
        shelf_manager.move_item(book1, ShelfType.PROGRESS)
        time.sleep(0.001)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 4)
        self.assertEqual(log.last().metadata, {"progress": 10})
        shelf_manager.move_item(book1, ShelfType.PROGRESS, metadata={"progress": 90})
        time.sleep(0.001)
        log = shelf_manager.get_log_for_item(book1)
        self.assertEqual(log.count(), 5)
        self.assertEqual(Mark(user, book1).visibility, 0)
        shelf_manager.move_item(
            book1, ShelfType.PROGRESS, metadata={"progress": 90}, visibility=1
        )
        time.sleep(0.001)
        self.assertEqual(Mark(user, book1).visibility, 1)
        self.assertEqual(shelf_manager.get_log_for_item(book1).count(), 5)

        # test silence mark mode -> no log
        shelf_manager.move_item(book1, ShelfType.WISHLIST, silence=True)
        self.assertEqual(log.count(), 5)
        shelf_manager.move_item(book1, ShelfType.PROGRESS, silence=True)
        self.assertEqual(log.count(), 5)
        # test delete one log
        first_log = log.first()
        Mark(user, book1).delete_log(first_log.id)
        self.assertEqual(log.count(), 4)
        # # test delete mark -> leave one log: 移除标记
        # Mark(user, book1).delete()
        # self.assertEqual(log.count(), 1)
        # # test delete all logs
        # shelf_manager.move_item(book1, ShelfType.PROGRESS)
        # self.assertEqual(log.count(), 2)
        # Mark(user, book1).delete(silence=True)
        # self.assertEqual(log.count(), 0)


class TagTest(TestCase):
    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.movie1 = Edition.objects.create(title="Hyperion, The Movie")
        self.user1 = User.register(mastodon_site="site", mastodon_username="name")
        self.user2 = User.register(mastodon_site="site2", mastodon_username="name2")
        self.user3 = User.register(mastodon_site="site2", mastodon_username="name3")
        pass

    def test_user_tag(self):
        t1 = "tag 1"
        t2 = "tag 2"
        t3 = "tag 3"
        TagManager.tag_item_by_user(self.book1, self.user2, [t1, t3])
        self.assertEqual(self.book1.tags, [t1, t3])
        TagManager.tag_item_by_user(self.book1, self.user2, [t2, t3])
        self.assertEqual(self.book1.tags, [t2, t3])


class MarkTest(TestCase):
    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.user1 = User.register(mastodon_site="site", mastodon_username="name")
        pref = self.user1.preference
        pref.default_visibility = 2
        pref.save()

    def test_mark(self):
        mark = Mark(self.user1, self.book1)
        self.assertEqual(mark.shelf_type, None)
        self.assertEqual(mark.shelf_label, None)
        self.assertEqual(mark.comment_text, None)
        self.assertEqual(mark.rating_grade, None)
        self.assertEqual(mark.visibility, 2)
        self.assertEqual(mark.review, None)
        self.assertEqual(mark.tags, [])
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, 1)

        mark = Mark(self.user1, self.book1)
        self.assertEqual(mark.shelf_type, ShelfType.WISHLIST)
        self.assertEqual(mark.shelf_label, "想读的书")
        self.assertEqual(mark.comment_text, "a gentle comment")
        self.assertEqual(mark.rating_grade, 9)
        self.assertEqual(mark.visibility, 1)
        self.assertEqual(mark.review, None)
        self.assertEqual(mark.tags, [])

        review = Review.review_item_by_user(self.book1, self.user1, "Critic", "Review")
        mark = Mark(self.user1, self.book1)
        self.assertEqual(mark.review, review)

        TagManager.tag_item_by_user(self.book1, self.user1, [" Sci-Fi ", " fic "])
        mark = Mark(self.user1, self.book1)
        self.assertEqual(mark.tags, ["Sci-Fi", "fic"])
