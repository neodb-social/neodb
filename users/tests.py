from django.conf import settings
from django.test import TestCase

from takahe.utils import Takahe

from .models import *


class UserTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.alice = User.register(
            mastodon_site="MySpace", mastodon_username="Alice", username="alice"
        ).identity
        self.bob = User.register(
            mastodon_site="KKCity", mastodon_username="Bob", username="bob"
        ).identity
        self.domain = settings.SITE_INFO.get("site_domain")

    def test_handle(self):
        self.assertEqual(APIdentity.get_by_handle("Alice"), self.alice)
        self.assertEqual(APIdentity.get_by_handle("@alice"), self.alice)
        self.assertEqual(APIdentity.get_by_handle("Alice@MySpace", True), self.alice)
        self.assertEqual(APIdentity.get_by_handle("alice@myspace", True), self.alice)
        self.assertEqual(APIdentity.get_by_handle("@alice@" + self.domain), self.alice)
        self.assertEqual(APIdentity.get_by_handle("@Alice@" + self.domain), self.alice)
        self.assertRaises(
            APIdentity.DoesNotExist, APIdentity.get_by_handle, "@Alice@MySpace"
        )
        self.assertRaises(
            APIdentity.DoesNotExist, APIdentity.get_by_handle, "@alice@KKCity"
        )

    def test_fetch(self):
        pass

    def test_follow(self):
        self.alice.follow(self.bob)
        Takahe._force_state_cycle()
        self.assertTrue(self.alice.is_following(self.bob))
        self.assertTrue(self.bob.is_followed_by(self.alice))
        self.assertEqual(self.alice.following, [self.bob.pk])
        self.assertEqual(self.bob.followers, [self.alice.pk])

        self.alice.unfollow(self.bob)
        Takahe._force_state_cycle()
        self.assertFalse(self.alice.is_following(self.bob))
        self.assertFalse(self.bob.is_followed_by(self.alice))
        self.assertEqual(self.alice.following, [])
        self.assertEqual(self.bob.followers, [])

    def test_mute(self):
        self.alice.mute(self.bob)
        Takahe._force_state_cycle()
        self.assertTrue(self.alice.is_muting(self.bob))
        self.assertEqual(self.alice.ignoring, [self.bob.pk])
        self.assertEqual(self.alice.rejecting, [])

    def test_block(self):
        self.alice.block(self.bob)
        Takahe._force_state_cycle()
        self.assertTrue(self.alice.is_blocking(self.bob))
        self.assertTrue(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.rejecting, [self.bob.pk])
        self.assertEqual(self.alice.ignoring, [self.bob.pk])

        self.alice.unblock(self.bob)
        Takahe._force_state_cycle()
        self.assertFalse(self.alice.is_blocking(self.bob))
        self.assertFalse(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.rejecting, [])
        self.assertEqual(self.alice.ignoring, [])

    # def test_external_domain_block(self):
    #     self.alice.mastodon_domain_blocks.append(self.bob.mastodon_site)
    #     self.alice.save()
    #     self.assertTrue(self.alice.is_blocking(self.bob))
    #     self.assertTrue(self.bob.is_blocked_by(self.alice))
    #     self.assertEqual(self.alice.merged_rejecting_ids(), [self.bob.pk])
    #     self.alice.merge_relationships()
    #     self.assertEqual(self.alice.rejecting, [self.bob.pk])
    #     self.alice.save()
    #     self.assertEqual(User.merge_rejected_by(), 1)
    #     self.bob.refresh_from_db()
    #     self.assertEqual(self.bob.rejecting, [self.alice.pk])

    #     self.alice.mastodon_domain_blocks.remove(self.bob.mastodon_site)
    #     self.assertFalse(self.alice.is_blocking(self.bob))
    #     self.assertFalse(self.bob.is_blocked_by(self.alice))
    #     self.assertEqual(self.alice.merged_rejecting_ids(), [])
