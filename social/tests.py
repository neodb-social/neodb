from django.test import TestCase
from catalog.models import *
from journal.models import *
from .models import *
from users.models import User


class SocialTest(TestCase):
    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.movie = Edition.objects.create(title="Fight Club")
        self.alice = User.objects.create(
            mastodon_site="MySpace", mastodon_username="Alice"
        )
        self.bob = User.objects.create(mastodon_site="KKCity", mastodon_username="Bob")

    def test_timeline(self):
        # alice see 0 activity in timeline in the beginning
        timeline = self.alice.activity_manager.get_timeline()
        self.assertEqual(len(timeline), 0)

        # 1 activity after adding first book to shelf
        self.alice.shelf_manager.move_item(self.book1, ShelfType.WISHLIST, visibility=1)
        timeline = self.alice.activity_manager.get_timeline()
        self.assertEqual(len(timeline), 1)

        # 2 activities after adding second book to shelf
        self.alice.shelf_manager.move_item(self.book2, ShelfType.WISHLIST)
        timeline = self.alice.activity_manager.get_timeline()
        self.assertEqual(len(timeline), 2)

        # 2 activities after change first mark
        self.alice.shelf_manager.move_item(self.book1, ShelfType.PROGRESS)
        timeline = self.alice.activity_manager.get_timeline()
        self.assertEqual(len(timeline), 2)

        # bob see 0 activity in timeline in the beginning
        timeline2 = self.bob.activity_manager.get_timeline()
        self.assertEqual(len(timeline2), 0)

        # bob follows alice, see 2 activities
        self.bob.mastodon_following = ["Alice@MySpace"]
        self.alice.mastodon_follower = ["Bob@KKCity"]
        self.bob.following = self.bob.get_following_ids()
        timeline2 = self.bob.activity_manager.get_timeline()
        self.assertEqual(len(timeline2), 2)

        # alice:3 bob:2 after alice adding second book to shelf as private
        self.alice.shelf_manager.move_item(self.movie, ShelfType.WISHLIST, visibility=2)
        timeline = self.alice.activity_manager.get_timeline()
        self.assertEqual(len(timeline), 3)
        timeline2 = self.bob.activity_manager.get_timeline()
        self.assertEqual(len(timeline2), 2)
