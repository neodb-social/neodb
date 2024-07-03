from django.test import TestCase

from catalog.models import *
from journal.models import *
from takahe.utils import Takahe
from users.models import User

from .models import *


class SocialTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.movie = Edition.objects.create(title="Fight Club")
        self.alice = User.register(username="Alice")
        self.bob = User.register(username="Bob")

    def test_timeline(self):
        alice_feed = self.alice.identity.activity_manager
        bob_feed = self.bob.identity.activity_manager

        # alice see 0 activity in timeline in the beginning
        self.assertEqual(len(alice_feed.get_timeline()), 0)

        # 1 activity after adding first book to shelf
        Mark(self.alice.identity, self.book1).update(ShelfType.WISHLIST, visibility=1)
        self.assertEqual(len(alice_feed.get_timeline()), 1)

        # 2 activities after adding second book to shelf
        Mark(self.alice.identity, self.book2).update(ShelfType.WISHLIST)
        self.assertEqual(len(alice_feed.get_timeline()), 2)

        # 2 activities after change first mark
        Mark(self.alice.identity, self.book1).update(ShelfType.PROGRESS)
        self.assertEqual(len(alice_feed.get_timeline()), 2)

        # bob see 0 activity in timeline in the beginning
        self.assertEqual(len(bob_feed.get_timeline()), 0)

        # bob follows alice, see 2 activities
        self.bob.identity.follow(self.alice.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 2)

        # bob mute, then unmute alice
        self.bob.identity.mute(self.alice.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 0)
        self.bob.identity.unmute(self.alice.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 2)

        # alice:3 bob:2 after alice adding second book to shelf as private
        Mark(self.alice.identity, self.movie).update(ShelfType.WISHLIST, visibility=2)
        self.assertEqual(len(alice_feed.get_timeline()), 3)
        self.assertEqual(len(bob_feed.get_timeline()), 2)

        # alice mute bob
        self.alice.identity.mute(self.bob.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 2)

        # bob unfollow alice
        self.bob.identity.unfollow(self.alice.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 0)

        # bob follow alice
        self.bob.identity.follow(self.alice.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 2)

        # alice block bob
        self.alice.identity.block(self.bob.identity)
        Takahe._force_state_cycle()
        self.assertEqual(len(bob_feed.get_timeline()), 0)
