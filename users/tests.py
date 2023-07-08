from django.test import TestCase
from .models import *


class UserTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create(
            mastodon_site="MySpace", mastodon_username="Alice"
        )
        self.bob = User.objects.create(mastodon_site="KKCity", mastodon_username="Bob")

    def test_local_follow(self):
        self.assertTrue(self.alice.follow(self.bob))
        self.assertTrue(
            Follow.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertEqual(self.alice.merged_following_ids(), [self.bob.pk])
        self.assertEqual(self.alice.following, [self.bob.pk])
        self.assertTrue(self.alice.is_following(self.bob))
        self.assertTrue(self.bob.is_followed_by(self.alice))

        self.assertFalse(self.alice.follow(self.bob))
        self.assertEqual(
            Follow.objects.filter(owner=self.alice, target=self.bob).count(), 1
        )
        self.assertEqual(self.alice.following, [self.bob.pk])

        self.assertTrue(self.alice.unfollow(self.bob))
        self.assertFalse(
            Follow.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertFalse(self.alice.is_following(self.bob))
        self.assertFalse(self.bob.is_followed_by(self.alice))
        self.assertEqual(self.alice.following, [])

    def test_locked(self):
        self.bob.mastodon_locked = True
        self.bob.save()
        self.assertFalse(self.alice.follow(self.bob))
        self.bob.mastodon_locked = False
        self.bob.save()
        self.assertTrue(self.alice.follow(self.bob))
        self.assertTrue(self.alice.is_following(self.bob))
        self.bob.mastodon_locked = True
        self.bob.save()
        self.assertFalse(self.alice.is_following(self.bob))

    def test_external_follow(self):
        self.alice.mastodon_following.append(self.bob.mastodon_acct)
        self.alice.merge_relationships()
        self.alice.save()
        self.assertTrue(self.alice.is_following(self.bob))
        self.assertEqual(self.alice.following, [self.bob.pk])
        self.assertFalse(self.alice.follow(self.bob))

        self.alice.mastodon_following.remove(self.bob.mastodon_acct)
        self.alice.merge_relationships()
        self.alice.save()
        self.assertFalse(self.alice.is_following(self.bob))
        self.assertEqual(self.alice.following, [])
        self.assertTrue(self.alice.follow(self.bob))
        self.assertTrue(self.alice.is_following(self.bob))

    def test_local_mute(self):
        self.alice.mute(self.bob)
        self.assertTrue(Mute.objects.filter(owner=self.alice, target=self.bob).exists())
        self.assertEqual(self.alice.merged_muting_ids(), [self.bob.pk])
        self.assertEqual(self.alice.ignoring, [self.bob.pk])
        self.assertTrue(self.alice.is_muting(self.bob))

        self.alice.mute(self.bob)
        self.assertEqual(
            Mute.objects.filter(owner=self.alice, target=self.bob).count(), 1
        )
        self.assertEqual(self.alice.ignoring, [self.bob.pk])

        self.alice.unmute(self.bob)
        self.assertFalse(
            Mute.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertFalse(self.alice.is_muting(self.bob))
        self.assertEqual(self.alice.ignoring, [])
        self.assertEqual(self.alice.merged_muting_ids(), [])

    def test_external_mute(self):
        self.alice.mastodon_mutes.append(self.bob.mastodon_acct)
        self.alice.save()
        self.assertTrue(self.alice.is_muting(self.bob))
        self.assertEqual(self.alice.merged_muting_ids(), [self.bob.pk])

        self.alice.mastodon_mutes.remove(self.bob.mastodon_acct)
        self.assertFalse(self.alice.is_muting(self.bob))
        self.assertEqual(self.alice.merged_muting_ids(), [])

    def test_local_block_follow(self):
        self.alice.block(self.bob)
        self.assertEqual(self.bob.follow(self.alice), False)
        self.alice.unblock(self.bob)
        self.assertEqual(self.bob.follow(self.alice), True)
        self.assertEqual(self.bob.following, [self.alice.pk])
        self.alice.block(self.bob)
        self.assertEqual(self.bob.following, [])

    def test_local_block(self):
        self.alice.block(self.bob)
        self.assertTrue(
            Block.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertEqual(self.alice.merged_rejecting_ids(), [self.bob.pk])
        self.assertEqual(self.alice.ignoring, [self.bob.pk])
        self.assertTrue(self.alice.is_blocking(self.bob))
        self.assertTrue(self.bob.is_blocked_by(self.alice))

        self.alice.block(self.bob)
        self.assertEqual(
            Block.objects.filter(owner=self.alice, target=self.bob).count(), 1
        )
        self.assertEqual(self.alice.ignoring, [self.bob.pk])

        self.alice.unblock(self.bob)
        self.assertFalse(
            Block.objects.filter(owner=self.alice, target=self.bob).exists()
        )
        self.assertFalse(self.alice.is_blocking(self.bob))
        self.assertFalse(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.ignoring, [])
        self.assertEqual(self.alice.merged_rejecting_ids(), [])

    def test_external_block(self):
        self.bob.follow(self.alice)
        self.assertEqual(self.bob.following, [self.alice.pk])
        self.alice.mastodon_blocks.append(self.bob.mastodon_acct)
        self.alice.save()
        self.assertTrue(self.alice.is_blocking(self.bob))
        self.assertTrue(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.merged_rejecting_ids(), [self.bob.pk])
        self.alice.merge_relationships()
        self.assertEqual(self.alice.rejecting, [self.bob.pk])
        self.alice.save()
        self.assertEqual(self.bob.following, [self.alice.pk])
        self.assertEqual(self.bob.rejecting, [])
        self.assertEqual(User.merge_rejected_by(), 2)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.rejecting, [self.alice.pk])
        self.assertEqual(self.bob.following, [])

        self.alice.mastodon_blocks.remove(self.bob.mastodon_acct)
        self.assertFalse(self.alice.is_blocking(self.bob))
        self.assertFalse(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.merged_rejecting_ids(), [])

    def test_external_domain_block(self):
        self.alice.mastodon_domain_blocks.append(self.bob.mastodon_site)
        self.alice.save()
        self.assertTrue(self.alice.is_blocking(self.bob))
        self.assertTrue(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.merged_rejecting_ids(), [self.bob.pk])
        self.alice.merge_relationships()
        self.assertEqual(self.alice.rejecting, [self.bob.pk])
        self.alice.save()
        self.assertEqual(User.merge_rejected_by(), 1)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.rejecting, [self.alice.pk])

        self.alice.mastodon_domain_blocks.remove(self.bob.mastodon_site)
        self.assertFalse(self.alice.is_blocking(self.bob))
        self.assertFalse(self.bob.is_blocked_by(self.alice))
        self.assertEqual(self.alice.merged_rejecting_ids(), [])
