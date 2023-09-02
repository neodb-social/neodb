import re
import uuid
from functools import cached_property

import django.dispatch
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import connection, models
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.baseconv import base62
from django.utils.translation import gettext_lazy as _
from markdownx.models import MarkdownxField
from polymorphic.models import PolymorphicModel

from catalog.collection.models import Collection as CatalogCollection
from catalog.common import jsondata
from catalog.common.models import Item, ItemCategory
from catalog.common.utils import DEFAULT_ITEM_COVER, piece_cover_path
from catalog.models import *
from mastodon.api import share_review
from users.models import User

from .mixins import UserOwnedObjectMixin
from .renderers import render_md, render_text

_logger = logging.getLogger(__name__)


class VisibilityType(models.IntegerChoices):
    Public = 0, _("公开")
    Follower_Only = 1, _("仅关注者")
    Private = 2, _("仅自己")


def q_visible_to(viewer, owner):
    if viewer == owner:
        return Q()
    # elif viewer.is_blocked_by(owner):
    #     return Q(pk__in=[])
    elif viewer.is_authenticated and viewer.is_following(owner):
        return Q(visibility__in=[0, 1])
    else:
        return Q(visibility=0)


def max_visiblity_to(viewer, owner):
    if viewer == owner:
        return 2
    # elif viewer.is_blocked_by(owner):
    #     return Q(pk__in=[])
    elif viewer.is_authenticated and viewer.is_following(owner):
        return 1
    else:
        return 0


def query_visible(user):
    return (
        (
            Q(visibility=0)
            | Q(owner_id__in=user.following, visibility=1)
            | Q(owner_id=user.id)
        )
        & ~Q(owner_id__in=user.ignoring)
        if user.is_authenticated
        else Q(visibility=0)
    )


def query_following(user):
    return Q(owner_id__in=user.following, visibility__lt=2) | Q(owner_id=user.id)


def query_item_category(item_category):
    classes = item_categories()[item_category]
    # q = Q(item__instance_of=classes[0])
    # for cls in classes[1:]:
    #     q = q | Q(instance_of=cls)
    # return q
    ct = item_content_types()
    contenttype_ids = [ct[cls] for cls in classes]
    return Q(item__polymorphic_ctype__in=contenttype_ids)


# class ImportStatus(Enum):
#     QUEUED = 0
#     PROCESSING = 1
#     FINISHED = 2


# class ImportSession(models.Model):
#     owner = models.ForeignKey(User, on_delete=models.CASCADE)
#     status = models.PositiveSmallIntegerField(default=ImportStatus.QUEUED)
#     importer = models.CharField(max_length=50)
#     file = models.CharField()
#     default_visibility = models.PositiveSmallIntegerField()
#     total = models.PositiveIntegerField()
#     processed = models.PositiveIntegerField()
#     skipped = models.PositiveIntegerField()
#     imported = models.PositiveIntegerField()
#     failed = models.PositiveIntegerField()
#     logs = models.JSONField(default=list)
#     created_time = models.DateTimeField(auto_now_add=True)
#     edited_time = models.DateTimeField(auto_now=True)

#     class Meta:
#         indexes = [
#             models.Index(fields=["owner", "importer", "created_time"]),
#         ]


class Piece(PolymorphicModel, UserOwnedObjectMixin):
    url_path = "p"  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    @property
    def uuid(self):
        return base62.encode(self.uid.int)

    @property
    def url(self):
        return f"/{self.url_path}/{self.uuid}" if self.url_path else None

    @property
    def absolute_url(self):
        return (settings.SITE_INFO["site_url"] + self.url) if self.url_path else None

    @property
    def api_url(self):
        return f"/api/{self.url}" if self.url_path else None

    @property
    def like_count(self):
        return self.likes.all().count()

    @classmethod
    def get_by_url(cls, url_or_b62):
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            obj = cls.objects.get(uid=uuid.UUID(int=base62.decode(b62)))
        except:
            obj = None
        return obj


class Content(Piece):
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now=True   FIXME revert this after migration
    metadata = models.JSONField(default=dict)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.uuid}@{self.item}"

    class Meta:
        abstract = True
