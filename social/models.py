"""
Models for Social app

DataSignalManager captures create/update/(soft/hard)delete/add/remove from Journal app, and generate Activity objects,
ActivityManager generates chronological view for user and, in future, ActivityStreams

"""

from django.db import models
from django.utils import timezone

from journal.models import (
    Piece,
    UserOwnedObjectMixin,
)
from users.models import APIdentity


class ActivityTemplate(models.TextChoices):
    MarkItem = "mark_item"
    ReviewItem = "review_item"
    CreateCollection = "create_collection"
    LikeCollection = "like_collection"
    FeatureCollection = "feature_collection"
    CommentChildItem = "comment_child_item"


class LocalActivity(models.Model, UserOwnedObjectMixin):
    owner = models.ForeignKey(APIdentity, on_delete=models.CASCADE)
    visibility = models.PositiveSmallIntegerField(default=0)  # type: ignore
    template = models.CharField(
        blank=False, choices=ActivityTemplate.choices, max_length=50
    )
    action_object = models.ForeignKey(Piece, on_delete=models.CASCADE)
    created_time = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        index_together = [
            ["owner", "created_time"],
        ]

    def __str__(self):
        return f"Activity [{self.owner}:{self.template}:{self.action_object}]"
