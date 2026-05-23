from typing import Optional

from django.db import models


class HashtagFollowQuerySet(models.QuerySet):
    def by_hashtags(self, hashtags: list[str]):
        return self.filter(hashtag_id__in=hashtags)

    def by_identity(self, identity):
        return self.filter(identity=identity)


class HashtagFollowManager(models.Manager):
    def get_queryset(self):
        return HashtagFollowQuerySet(self.model, using=self._db)

    def by_hashtags(self, hashtags: list[str]):
        return self.get_queryset().by_hashtags(hashtags)

    def by_identity(self, identity):
        return self.get_queryset().by_identity(identity)


class HashtagFollow(models.Model):
    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="hashtag_follows",
    )
    hashtag = models.ForeignKey(
        "activities.Hashtag",
        on_delete=models.CASCADE,
        related_name="followers",
    )

    created = models.DateTimeField(auto_now_add=True)

    objects = HashtagFollowManager()

    class Meta:
        unique_together = [("identity", "hashtag")]

    def __str__(self):
        return f"#{self.id}: {self.identity} → {self.hashtag_id}"

    ### Alternate fetchers/constructors ###

    @classmethod
    def maybe_get(cls, identity, hashtag) -> Optional["HashtagFollow"]:
        """
        Returns a hashtag follow if it exists between identity and hashtag
        """
        try:
            return HashtagFollow.objects.get(identity=identity, hashtag=hashtag)
        except HashtagFollow.DoesNotExist:
            return None


class HashtagFeature(models.Model):
    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="hashtag_features",
    )
    hashtag = models.ForeignKey(
        "activities.Hashtag",
        on_delete=models.CASCADE,
        related_name="featurers",
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("identity", "hashtag")]

    def __str__(self):
        return f"#{self.id}: {self.identity} → {self.hashtag_id}"

    def get_targets(self):
        """
        Returns an iterable with Identities of followers that have unique
        shared_inbox among each other to be used as target.
        """
        targets = set(
            follow.source
            for follow in self.identity.inbound_follows.active().select_related(
                "source"
            )
        )

        # Fetch the full blocks and remove them as targets
        for block in (
            self.identity.outbound_blocks.active()
            .filter(mute=False)
            .select_related("target")
        ):
            try:
                targets.remove(block.target)
            except KeyError:
                pass

        deduped_targets = set()
        shared_inboxes = set()
        for target in targets:
            if target.local:
                # Local targets always gets the boosts
                # despite its creator locality
                deduped_targets.add(target)
            elif self.identity.local:
                # Dedupe the targets based on shared inboxes
                # (we only keep one per shared inbox)
                if not target.shared_inbox_uri:
                    deduped_targets.add(target)
                elif target.shared_inbox_uri not in shared_inboxes:
                    shared_inboxes.add(target.shared_inbox_uri)
                    deduped_targets.add(target)

        return deduped_targets

    def to_mastodon_json(self, domain=None):
        return {
            "id": str(self.pk),
            "statuses_count": 0,
            "last_status_at": "",
            **self.hashtag.to_mastodon_json(domain=domain),
        }
