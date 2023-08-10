"""
Models for Social app

DataSignalManager captures create/update/(soft/hard)delete/add/remove from Journal app, and generate Activity objects,
ActivityManager generates chronological view for user and, in future, ActivityStreams

"""

import logging
from functools import cached_property

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete
from django.utils import timezone

from catalog.common.models import Item
from journal.models import (
    Collection,
    Comment,
    FeaturedCollection,
    Like,
    Piece,
    Review,
    ShelfMember,
    UserOwnedObjectMixin,
)
from users.models import User

_logger = logging.getLogger(__name__)


class ActivityTemplate(models.TextChoices):
    MarkItem = "mark_item"
    ReviewItem = "review_item"
    CreateCollection = "create_collection"
    LikeCollection = "like_collection"
    FeatureCollection = "feature_collection"
    CommentChildItem = "comment_child_item"


class LocalActivity(models.Model, UserOwnedObjectMixin):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
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


class ActivityManager:
    def __init__(self, user):
        self.owner = user

    def get_timeline(self, before_time=None):
        following = [x for x in self.owner.following if x not in self.owner.ignoring]
        q = Q(owner_id__in=following, visibility__lt=2) | Q(owner=self.owner)
        if before_time:
            q = q & Q(created_time__lt=before_time)
        return (
            LocalActivity.objects.filter(q)
            .order_by("-created_time")
            .prefetch_related("action_object", "owner")
        )  # .select_related() https://github.com/django-polymorphic/django-polymorphic/pull/531

    @staticmethod
    def get_manager_for_user(user):
        return ActivityManager(user)


User.activity_manager = cached_property(ActivityManager.get_manager_for_user)  # type: ignore
User.activity_manager.__set_name__(User, "activity_manager")  # type: ignore


class DataSignalManager:
    processors = {}

    @staticmethod
    def save_handler(sender, instance, created, **kwargs):
        processor_class = DataSignalManager.processors.get(instance.__class__)

        if processor_class:
            processor = processor_class(instance)
            if created:
                if hasattr(processor, "created"):
                    processor.created()
            elif hasattr(processor, "updated"):
                processor.updated()

    @staticmethod
    def delete_handler(sender, instance, **kwargs):
        processor_class = DataSignalManager.processors.get(instance.__class__)
        if processor_class:
            processor = processor_class(instance)
            if hasattr(processor, "deleted"):
                processor.deleted()

    @staticmethod
    def add_handler_for_model(model):
        if settings.DISABLE_MODEL_SIGNAL:
            _logger.warn(
                f"{model.__name__} are not being indexed with DISABLE_MODEL_SIGNAL configuration"
            )
            return
        post_save.connect(DataSignalManager.save_handler, sender=model)
        pre_delete.connect(DataSignalManager.delete_handler, sender=model)

    @staticmethod
    def register(processor):
        DataSignalManager.add_handler_for_model(processor.model)
        DataSignalManager.processors[processor.model] = processor
        return processor


class DefaultActivityProcessor:
    model = None
    template = None

    def __init__(self, action_object):
        self.action_object = action_object

    def created(self):
        params = {
            "owner": self.action_object.owner,
            "visibility": self.action_object.visibility,
            "template": self.template,
            "action_object": self.action_object,
            "created_time": self.action_object.created_time,
        }
        LocalActivity.objects.create(**params)

    def updated(self):
        activity = LocalActivity.objects.filter(
            action_object=self.action_object
        ).first()
        if not activity:
            self.created()
        elif (
            activity.visibility != self.action_object.visibility
            or activity.created_time != activity.action_object.created_time
        ):
            activity.visibility = self.action_object.visibility
            activity.created_time = activity.action_object.created_time
            activity.save()


@DataSignalManager.register
class MarkProcessor(DefaultActivityProcessor):
    model = ShelfMember
    template = ActivityTemplate.MarkItem


@DataSignalManager.register
class ReviewProcessor(DefaultActivityProcessor):
    model = Review
    template = ActivityTemplate.ReviewItem


@DataSignalManager.register
class CollectionProcessor(DefaultActivityProcessor):
    model = Collection
    template = ActivityTemplate.CreateCollection


@DataSignalManager.register
class LikeCollectionProcessor(DefaultActivityProcessor):
    model = Like
    template = ActivityTemplate.LikeCollection

    def created(self):
        if isinstance(self.action_object.target, Collection):
            super().created()

    def updated(self):
        if isinstance(self.action_object.target, Collection):
            super().updated()


@DataSignalManager.register
class FeaturedCollectionProcessor(DefaultActivityProcessor):
    model = FeaturedCollection
    template = ActivityTemplate.FeatureCollection


@DataSignalManager.register
class CommentChildItemProcessor(DefaultActivityProcessor):
    model = Comment
    template = ActivityTemplate.CommentChildItem

    def created(self):
        if self.action_object.item.class_name in ["podcastepisode", "tvepisode"]:
            super().created()

    def updated(self):
        if self.action_object.item.class_name in ["podcastepisode", "tvepisode"]:
            super().updated()


def reset_social_visibility_for_user(user: User, visibility: int):
    LocalActivity.objects.filter(owner=user).update(visibility=visibility)
