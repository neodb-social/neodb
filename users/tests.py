from django.test import TestCase
from .models import *


class UserTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create(
            mastodon_site="MySpace", mastodon_username="Alice"
        )
        self.bob = User.objects.create(mastodon_site="KKCity", mastodon_username="Bob")

    def test_local_follow(self):
        self.alice.follow(self.bob)
        self.assertTrue(
            Follow.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertEqual(self.alice.following, [self.bob.pk])
        self.assertTrue(self.alice.is_following(self.bob))
        self.assertTrue(self.bob.is_followed_by(self.alice))

        self.alice.follow(self.bob)
        self.assertEqual(
            Follow.objects.filter(owner=self.alice, target=self.bob).count(), 1
        )
        self.assertEqual(self.alice.following, [self.bob.pk])

        self.alice.unfollow(self.bob)
        self.assertFalse(
            Follow.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertFalse(self.alice.is_following(self.bob))
        self.assertFalse(self.bob.is_followed_by(self.alice))
        self.assertEqual(self.alice.following, [])
