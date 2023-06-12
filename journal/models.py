from django.db import models
from polymorphic.models import PolymorphicModel
from mastodon.api import share_review
from users.models import User
from catalog.common.models import Item, ItemCategory
from .mixins import UserOwnedObjectMixin
from catalog.collection.models import Collection as CatalogCollection
from markdownx.models import MarkdownxField
from django.utils import timezone
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator
from functools import cached_property
from django.db.models import Count, Avg
from django.contrib.contenttypes.models import ContentType
import django.dispatch
import uuid
import re
from catalog.common.utils import DEFAULT_ITEM_COVER, piece_cover_path
from django.utils.baseconv import base62
from django.db.models import Q
from catalog.models import *
from django.contrib.contenttypes.models import ContentType
from .renderers import render_md, render_text
from catalog.common import jsondata
from django.db import connection

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
        Q(visibility=0)
        | Q(owner_id__in=user.following if user.is_authenticated else [], visibility=1)
        | Q(owner_id=user.id)
    )


def query_following(user):
    return Q(owner_id__in=user.following, visibility__lt=2) | Q(owner_id=user.id)


def query_item_category(item_category):
    classes = all_categories()[item_category]
    # q = Q(item__instance_of=classes[0])
    # for cls in classes[1:]:
    #     q = q | Q(instance_of=cls)
    # return q
    ct = all_content_types()
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
        return (settings.APP_WEBSITE + self.url) if self.url_path else None

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


class Like(Piece):
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now_add=True  FIXME revert this after migration
    edited_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now=True   FIXME revert this after migration
    target = models.ForeignKey(Piece, on_delete=models.CASCADE, related_name="likes")

    @staticmethod
    def user_liked_piece(user, piece):
        return Like.objects.filter(owner=user, target=piece).exists()

    @staticmethod
    def user_like_piece(user, piece):
        if not piece:
            return
        like = Like.objects.filter(owner=user, target=piece).first()
        if not like:
            like = Like.objects.create(owner=user, target=piece)
        return like

    @staticmethod
    def user_unlike_piece(user, piece):
        if not piece:
            return
        Like.objects.filter(owner=user, target=piece).delete()

    @staticmethod
    def user_likes_by_class(user, cls):
        ctype_id = ContentType.objects.get_for_model(cls)
        return Like.objects.filter(owner=user, target__polymorphic_ctype=ctype_id)


class Memo(Content):
    pass


class Comment(Content):
    text = models.TextField(blank=False, null=False)
    focus_item = models.ForeignKey(
        Item, on_delete=models.PROTECT, null=True, related_name="focused_comments"
    )

    @property
    def html(self):
        return render_text(self.text)

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating_by_user(self.item, self.owner)

    @cached_property
    def mark(self):
        m = Mark(self.owner, self.item)
        m.comment = self
        return m

    @property
    def item_url(self):
        if self.focus_item:
            return self.focus_item.get_absolute_url_with_position(
                self.metadata["position"]
            )
        else:
            return self.item.url

    @staticmethod
    def comment_item_by_user(item, user, text, visibility=0, created_time=None):
        comment = Comment.objects.filter(
            owner=user, item=item, focus_item__isnull=True
        ).first()
        if not text:
            if comment is not None:
                comment.delete()
                comment = None
        elif comment is None:
            comment = Comment.objects.create(
                owner=user,
                item=item,
                text=text,
                visibility=visibility,
                created_time=created_time or timezone.now(),
            )
        elif comment.text != text or comment.visibility != visibility:
            comment.text = text
            comment.visibility = visibility
            if created_time:
                comment.created_time = created_time
            comment.save()
        return comment


class Review(Content):
    url_path = "review"
    title = models.CharField(max_length=500, blank=False, null=False)
    body = MarkdownxField()

    @property
    def html_content(self):
        return render_md(self.body)

    @property
    def plain_content(self):
        html = render_md(self.body)
        return _RE_HTML_TAG.sub(
            " ", _RE_SPOILER_TAG.sub("***", html.replace("\n", " "))
        )

    @cached_property
    def mark(self):
        m = Mark(self.owner, self.item)
        m.review = self
        return m

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating_by_user(self.item, self.owner)

    @classmethod
    def review_item_by_user(
        cls,
        item,
        user,
        title,
        body,
        visibility=0,
        created_time=None,
        share_to_mastodon=False,
    ):
        if title is None:
            review = Review.objects.filter(owner=user, item=item).first()
            if review is not None:
                review.delete()
            return None
        defaults = {
            "title": title,
            "body": body,
            "visibility": visibility,
        }
        if created_time:
            defaults["created_time"] = (
                created_time if created_time < timezone.now() else timezone.now()
            )
        review, created = cls.objects.update_or_create(
            item=item, owner=user, defaults=defaults
        )
        if share_to_mastodon:
            share_review(review)
        return review


MIN_RATING_COUNT = 5


class Rating(Content):
    class Meta:
        unique_together = [["owner", "item"]]

    grade = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(10), MinValueValidator(1)], null=True
    )

    @staticmethod
    def get_rating_for_item(item):
        ids = item.child_item_ids + [item.id]
        stat = Rating.objects.filter(item_id__in=ids, grade__isnull=False).aggregate(
            average=Avg("grade"), count=Count("item")
        )
        return round(stat["average"], 1) if stat["count"] >= MIN_RATING_COUNT else None

    @staticmethod
    def get_rating_count_for_item(item):
        ids = item.child_item_ids + [item.id]
        stat = Rating.objects.filter(item_id__in=ids, grade__isnull=False).aggregate(
            count=Count("item")
        )
        return stat["count"]

    @staticmethod
    def rate_item_by_user(item, user, rating_grade, visibility=0):
        if rating_grade and (rating_grade < 1 or rating_grade > 10):
            raise ValueError(f"Invalid rating grade: {rating_grade}")
        rating = Rating.objects.filter(owner=user, item=item).first()
        if not rating_grade:
            if rating:
                rating.delete()
                rating = None
        elif rating is None:
            rating = Rating.objects.create(
                owner=user, item=item, grade=rating_grade, visibility=visibility
            )
        elif rating.grade != rating_grade or rating.visibility != visibility:
            rating.visibility = visibility
            rating.grade = rating_grade
            rating.save()
        return rating

    @staticmethod
    def get_item_rating_by_user(item, user):
        rating = Rating.objects.filter(owner=user, item=item).first()
        return (rating.grade or None) if rating else None

    @staticmethod
    def get_rating_distribution_for_item(item):
        stat = (
            Rating.objects.filter(item=item, grade__isnull=False)
            .values("grade")
            .annotate(count=Count("grade"))
            .order_by("grade")
        )
        g = [0] * 11
        t = 0
        for s in stat:
            g[s["grade"]] = s["count"]
            t += s["count"]
        if t < MIN_RATING_COUNT:
            return [0] * 5
        r = [
            100 * (g[1] + g[2]) // t,
            100 * (g[3] + g[4]) // t,
            100 * (g[5] + g[6]) // t,
            100 * (g[7] + g[8]) // t,
            100 * (g[9] + g[10]) // t,
        ]
        return r


Item.rating = property(Rating.get_rating_for_item)
Item.rating_count = property(Rating.get_rating_count_for_item)
Item.rating_dist = property(Rating.get_rating_distribution_for_item)


class Reply(Piece):
    reply_to_content = models.ForeignKey(
        Piece, on_delete=models.SET_NULL, related_name="replies", null=True
    )
    title = models.CharField(max_length=500, null=True)
    body = MarkdownxField()


"""
List (abstract class)
"""

list_add = django.dispatch.Signal()
list_remove = django.dispatch.Signal()


class List(Piece):
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now_add=True  FIXME revert this after migration
    edited_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now=True   FIXME revert this after migration
    metadata = models.JSONField(default=dict)

    class Meta:
        abstract = True

    # MEMBER_CLASS = None  # subclass must override this
    # subclass must add this:
    # items = models.ManyToManyField(Item, through='ListMember')

    @property
    def ordered_members(self):
        return self.members.all().order_by("position")

    @property
    def ordered_items(self):
        return self.items.all().order_by(
            self.MEMBER_CLASS.__name__.lower() + "__position"
        )

    @property
    def recent_items(self):
        return self.items.all().order_by(
            "-" + self.MEMBER_CLASS.__name__.lower() + "__created_time"
        )

    @property
    def recent_members(self):
        return self.members.all().order_by("-created_time")

    def get_member_for_item(self, item):
        return self.members.filter(item=item).first()

    def get_summary(self):
        summary = {k: 0 for k in ItemCategory.values}
        for c in self.recent_items:
            summary[c.category] += 1
        return summary

    def append_item(self, item, **params):
        """
        named metadata fields should be specified directly, not in metadata dict!
        e.g. collection.append_item(item, note="abc") works, but collection.append_item(item, metadata={"note":"abc"}) doesn't
        """
        if item is None:
            return None
        member = self.get_member_for_item(item)
        if member:
            return member
        ml = self.ordered_members
        p = {"parent": self}
        p.update(params)
        member = self.MEMBER_CLASS.objects.create(
            owner=self.owner,
            position=ml.last().position + 1 if ml.count() else 1,
            item=item,
            **p,
        )
        list_add.send(sender=self.__class__, instance=self, item=item, member=member)
        return member

    def remove_item(self, item):
        member = self.get_member_for_item(item)
        if member:
            list_remove.send(
                sender=self.__class__, instance=self, item=item, member=member
            )
            member.delete()

    def update_member_order(self, ordered_member_ids):
        members = self.ordered_members
        for m in self.members.all():
            try:
                i = ordered_member_ids.index(m.id)
                if m.position != i + 1:
                    m.position = i + 1
                    m.save()
            except ValueError:
                pass

    def move_up_item(self, item):
        members = self.ordered_members
        member = self.get_member_for_item(item)
        if member:
            other = members.filter(position__lt=member.position).last()
            if other:
                p = other.position
                other.position = member.position
                member.position = p
                other.save()
                member.save()

    def move_down_item(self, item):
        members = self.ordered_members
        member = self.get_member_for_item(item)
        if member:
            other = members.filter(position__gt=member.position).first()
            if other:
                p = other.position
                other.position = member.position
                member.position = p
                other.save()
                member.save()

    def update_item_metadata(self, item, metadata):
        member = self.get_member_for_item(item)
        if member:
            member.metadata = metadata
            member.save()


class ListMember(Piece):
    """
    ListMember - List class's member class
    It's an abstract class, subclass must add this:

    parent = models.ForeignKey('List', related_name='members', on_delete=models.CASCADE)
    """

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
    position = models.PositiveIntegerField()

    @cached_property
    def mark(self):
        m = Mark(self.owner, self.item)
        return m

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.id}:{self.position} ({self.item})"


"""
Shelf
"""


class ShelfType(models.TextChoices):
    WISHLIST = ("wishlist", "未开始")
    PROGRESS = ("progress", "进行中")
    COMPLETE = ("complete", "完成")
    # DISCARDED = ('discarded', '放弃')


ShelfTypeNames = [
    [ItemCategory.Book, ShelfType.WISHLIST, _("想读")],
    [ItemCategory.Book, ShelfType.PROGRESS, _("在读")],
    [ItemCategory.Book, ShelfType.COMPLETE, _("读过")],
    [ItemCategory.Movie, ShelfType.WISHLIST, _("想看")],
    [ItemCategory.Movie, ShelfType.PROGRESS, _("在看")],
    [ItemCategory.Movie, ShelfType.COMPLETE, _("看过")],
    [ItemCategory.TV, ShelfType.WISHLIST, _("想看")],
    [ItemCategory.TV, ShelfType.PROGRESS, _("在看")],
    [ItemCategory.TV, ShelfType.COMPLETE, _("看过")],
    [ItemCategory.Music, ShelfType.WISHLIST, _("想听")],
    [ItemCategory.Music, ShelfType.PROGRESS, _("在听")],
    [ItemCategory.Music, ShelfType.COMPLETE, _("听过")],
    [ItemCategory.Game, ShelfType.WISHLIST, _("想玩")],
    [ItemCategory.Game, ShelfType.PROGRESS, _("在玩")],
    [ItemCategory.Game, ShelfType.COMPLETE, _("玩过")],
    [ItemCategory.Podcast, ShelfType.WISHLIST, _("想听")],
    [ItemCategory.Podcast, ShelfType.PROGRESS, _("在听")],
    [ItemCategory.Podcast, ShelfType.COMPLETE, _("听过")],
    [ItemCategory.Performance, ShelfType.WISHLIST, _("想看")],
    # disable progress shelf for performance
    [ItemCategory.Performance, ShelfType.PROGRESS, _("")],
    [ItemCategory.Performance, ShelfType.COMPLETE, _("看过")],
]


class ShelfMember(ListMember):
    parent = models.ForeignKey(
        "Shelf", related_name="members", on_delete=models.CASCADE
    )

    class Meta:
        unique_together = [["owner", "item"]]
        indexes = [
            models.Index(fields=["parent_id", "visibility", "created_time"]),
        ]

    @cached_property
    def mark(self):
        m = Mark(self.owner, self.item)
        m.shelfmember = self
        return m

    @property
    def shelf_label(self):
        return ShelfManager.get_label(self.parent.shelf_type, self.item.category)

    @property
    def shelf_type(self):
        return self.parent.shelf_type

    @property
    def rating_grade(self):
        return self.mark.rating_grade

    @property
    def comment_text(self):
        return self.mark.comment_text

    @property
    def tags(self):
        return self.mark.tags


class Shelf(List):
    class Meta:
        unique_together = [["owner", "shelf_type"]]

    MEMBER_CLASS = ShelfMember
    items = models.ManyToManyField(Item, through="ShelfMember", related_name="+")
    shelf_type = models.CharField(
        choices=ShelfType.choices, max_length=100, null=False, blank=False
    )

    def __str__(self):
        return f"{self.id} [{self.owner} {self.shelf_type} list]"


class ShelfLogEntry(models.Model):
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    shelf_type = models.CharField(choices=ShelfType.choices, max_length=100, null=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    timestamp = models.DateTimeField()  # this may later be changed by user
    metadata = models.JSONField(default=dict)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.owner}:{self.shelf_type}:{self.item.uuid}:{self.timestamp}:{self.metadata}"

    @property
    def action_label(self):
        if self.shelf_type:
            return ShelfManager.get_action_label(self.shelf_type, self.item.category)
        else:
            return _("移除标记")


class ShelfManager:
    """
    ShelfManager

    all shelf operations should go thru this class so that ShelfLogEntry can be properly populated
    ShelfLogEntry can later be modified if user wish to change history
    """

    def __init__(self, user):
        self.owner = user
        qs = Shelf.objects.filter(owner=self.owner)
        self.shelf_list = {v.shelf_type: v for v in qs}
        if len(self.shelf_list) == 0:
            self.initialize()

    def initialize(self):
        for qt in ShelfType:
            self.shelf_list[qt] = Shelf.objects.create(owner=self.owner, shelf_type=qt)

    def locate_item(self, item) -> ShelfMember:
        return ShelfMember.objects.filter(item=item, owner=self.owner).first()

    def move_item(self, item, shelf_type, visibility=0, metadata=None):
        # shelf_type=None means remove from current shelf
        # metadata=None means no change
        if not item:
            raise ValueError("empty item")
        new_shelfmember = None
        last_shelfmember = self.locate_item(item)
        last_shelf = last_shelfmember.parent if last_shelfmember else None
        last_metadata = last_shelfmember.metadata if last_shelfmember else None
        last_visibility = last_shelfmember.visibility if last_shelfmember else None
        shelf = self.shelf_list[shelf_type] if shelf_type else None
        changed = False
        if last_shelf != shelf:  # change shelf
            changed = True
            if last_shelf:
                last_shelf.remove_item(item)
            if shelf:
                new_shelfmember = shelf.append_item(
                    item, visibility=visibility, metadata=metadata or {}
                )
        elif last_shelf is None:
            raise ValueError("empty shelf")
        else:
            new_shelfmember = last_shelfmember
            if metadata is not None and metadata != last_metadata:  # change metadata
                changed = True
                last_shelfmember.metadata = metadata
                last_shelfmember.visibility = visibility
                last_shelfmember.save()
            elif visibility != last_visibility:  # change visibility
                last_shelfmember.visibility = visibility
                last_shelfmember.save()
        if changed:
            if metadata is None:
                metadata = last_metadata or {}
            log_time = (
                new_shelfmember.created_time
                if new_shelfmember and new_shelfmember != last_shelfmember
                else timezone.now()
            )
            ShelfLogEntry.objects.create(
                owner=self.owner,
                shelf_type=shelf_type,
                item=item,
                metadata=metadata,
                timestamp=log_time,
            )
        return new_shelfmember

    def get_log(self):
        return ShelfLogEntry.objects.filter(owner=self.owner).order_by("timestamp")

    def get_log_for_item(self, item):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=item).order_by(
            "timestamp"
        )

    def get_shelf(self, shelf_type):
        return self.shelf_list[shelf_type]

    def get_latest_members(self, shelf_type, item_category=None):
        qs = self.shelf_list[shelf_type].members.all().order_by("-created_time")
        if item_category:
            return qs.filter(query_item_category(item_category))
        else:
            return qs

    # def get_items_on_shelf(self, item_category, shelf_type):
    #     shelf = (
    #         self.owner.shelf_set.all()
    #         .filter(item_category=item_category, shelf_type=shelf_type)
    #         .first()
    #     )
    #     return shelf.members.all().order_by

    @classmethod
    def get_action_label(cls, shelf_type, item_category):
        sts = [
            n[2] for n in ShelfTypeNames if n[0] == item_category and n[1] == shelf_type
        ]
        return sts[0] if sts else shelf_type

    @classmethod
    def get_label(cls, shelf_type, item_category):
        ic = ItemCategory(item_category).label
        st = cls.get_action_label(shelf_type, item_category)
        return (
            _("{shelf_label}的{item_category}").format(shelf_label=st, item_category=ic)
            if st
            else None
        )

    @staticmethod
    def get_manager_for_user(user):
        return ShelfManager(user)

    def get_calendar_data(self, max_visiblity):
        shelf_id = self.get_shelf(ShelfType.COMPLETE).pk
        timezone_offset = timezone.localtime(timezone.now()).strftime("%z")
        timezone_offset = timezone_offset[: len(timezone_offset) - 2]
        calendar_data = {}
        sql = "SELECT to_char(DATE(journal_shelfmember.created_time::timestamp AT TIME ZONE %s), 'YYYY-MM-DD') AS dat, django_content_type.model typ, COUNT(1) count FROM journal_shelfmember, catalog_item, django_content_type WHERE journal_shelfmember.item_id = catalog_item.id AND django_content_type.id = catalog_item.polymorphic_ctype_id AND parent_id = %s AND journal_shelfmember.created_time >= NOW() - INTERVAL '366 days' AND journal_shelfmember.visibility <= %s GROUP BY item_id, dat, typ;"
        with connection.cursor() as cursor:
            cursor.execute(sql, [timezone_offset, shelf_id, int(max_visiblity)])
            data = cursor.fetchall()
            for line in data:
                date = line[0]
                typ = line[1]
                if date not in calendar_data:
                    calendar_data[date] = {"items": []}
                if typ[:2] == "tv":
                    typ = "movie"
                elif typ == "album":
                    typ = "music"
                elif typ == "edition":
                    typ = "book"
                elif typ not in ["book", "movie", "music", "game"]:
                    typ = "other"
                if typ not in calendar_data[date]["items"]:
                    calendar_data[date]["items"].append(typ)
        return calendar_data


User.shelf_manager = cached_property(ShelfManager.get_manager_for_user)
User.shelf_manager.__set_name__(User, "shelf_manager")


"""
Collection
"""


class CollectionMember(ListMember):
    parent = models.ForeignKey(
        "Collection", related_name="members", on_delete=models.CASCADE
    )

    note = jsondata.CharField(_("备注"), null=True, blank=True)


_RE_HTML_TAG = re.compile(r"<[^>]*>")
_RE_SPOILER_TAG = re.compile(r'<(div|span)\sclass="spoiler">.*</(div|span)>')


class Collection(List):
    url_path = "collection"
    MEMBER_CLASS = CollectionMember
    catalog_item = models.OneToOneField(
        CatalogCollection, on_delete=models.PROTECT, related_name="journal_item"
    )
    title = models.CharField(_("标题"), max_length=1000, default="")
    brief = models.TextField(_("简介"), blank=True, default="")
    cover = models.ImageField(
        upload_to=piece_cover_path, default=DEFAULT_ITEM_COVER, blank=True
    )
    items = models.ManyToManyField(
        Item, through="CollectionMember", related_name="collections"
    )
    collaborative = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Editable by owner only / 1: Editable by bi-direction followers
    featured_by_users = models.ManyToManyField(
        to=User, related_name="featured_collections", through="FeaturedCollection"
    )

    @property
    def html(self):
        html = render_md(self.brief)
        return html

    @property
    def plain_description(self):
        html = render_md(self.brief)
        return _RE_HTML_TAG.sub(" ", html)

    def featured_by_user_since(self, user):
        f = FeaturedCollection.objects.filter(target=self, owner=user).first()
        return f.created_time if f else None

    def get_stats_for_user(self, user):
        items = list(self.members.all().values_list("item_id", flat=True))
        stats = {"total": len(items)}
        for st, shelf in user.shelf_manager.shelf_list.items():
            stats[st] = shelf.members.all().filter(item_id__in=items).count()
        stats["percentage"] = (
            round(stats["complete"] * 100 / stats["total"]) if stats["total"] else 0
        )
        return stats

    def get_progress_for_user(self, user):
        items = list(self.members.all().values_list("item_id", flat=True))
        if len(items) == 0:
            return 0
        shelf = user.shelf_manager.shelf_list["complete"]
        return round(
            shelf.members.all().filter(item_id__in=items).count() * 100 / len(items)
        )

    def save(self, *args, **kwargs):
        if getattr(self, "catalog_item", None) is None:
            self.catalog_item = CatalogCollection()
        if (
            self.catalog_item.title != self.title
            or self.catalog_item.brief != self.brief
        ):
            self.catalog_item.title = self.title
            self.catalog_item.brief = self.brief
            self.catalog_item.cover = self.cover
            self.catalog_item.save()
        super().save(*args, **kwargs)


class FeaturedCollection(Piece):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    target = models.ForeignKey(Collection, on_delete=models.CASCADE)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["owner", "target"]]

    @property
    def visibility(self):
        return self.target.visibility

    @cached_property
    def progress(self):
        return self.target.get_progress_for_user(self.owner)


"""
Tag
"""


class TagMember(ListMember):
    parent = models.ForeignKey("Tag", related_name="members", on_delete=models.CASCADE)

    class Meta:
        unique_together = [["parent", "item"]]


TagValidators = [RegexValidator(regex=r"\s+", inverse_match=True)]


class Tag(List):
    MEMBER_CLASS = TagMember
    items = models.ManyToManyField(Item, through="TagMember")
    title = models.CharField(
        max_length=100, null=False, blank=False, validators=TagValidators
    )
    # TODO case convert and space removal on save
    # TODO check on save

    class Meta:
        unique_together = [["owner", "title"]]

    @staticmethod
    def cleanup_title(title, replace=True):
        t = re.sub(r"\s+", " ", title.strip())
        return "_" if not title and replace else t

    @staticmethod
    def deep_cleanup_title(title):
        """Remove all non-word characters, only for public index purpose"""
        return re.sub(r"\W+", " ", title).strip()


class TagManager:
    @staticmethod
    def indexable_tags_for_item(item):
        tags = (
            item.tag_set.all()
            .filter(visibility=0)
            .values("title")
            .annotate(frequency=Count("owner"))
            .order_by("-frequency")[:20]
        )
        tag_titles = sorted(
            [
                t
                for t in set(map(lambda t: Tag.deep_cleanup_title(t["title"]), tags))
                if t
            ]
        )
        return tag_titles

    @staticmethod
    def all_tags_for_user(user, public_only=False):
        tags = (
            user.tag_set.all()
            .values("title")
            .annotate(frequency=Count("members__id"))
            .order_by("-frequency")
        )
        if public_only:
            tags = tags.filter(visibility=0)
        return list(map(lambda t: t["title"], tags))

    @staticmethod
    def tag_item_by_user(item, user, tag_titles, default_visibility=0):
        titles = set([Tag.cleanup_title(tag_title) for tag_title in tag_titles])
        current_titles = set(
            [m.parent.title for m in TagMember.objects.filter(owner=user, item=item)]
        )
        for title in titles - current_titles:
            tag = Tag.objects.filter(owner=user, title=title).first()
            if not tag:
                tag = Tag.objects.create(
                    owner=user, title=title, visibility=default_visibility
                )
            tag.append_item(item)
        for title in current_titles - titles:
            tag = Tag.objects.filter(owner=user, title=title).first()
            tag.remove_item(item)

    @staticmethod
    def get_item_tags_by_user(item, user):
        current_titles = [
            m.parent.title for m in TagMember.objects.filter(owner=user, item=item)
        ]
        return current_titles

    @staticmethod
    def add_tag_by_user(item, tag_title, user, default_visibility=0):
        title = Tag.cleanup_title(tag_title)
        tag = Tag.objects.filter(owner=user, title=title).first()
        if not tag:
            tag = Tag.objects.create(
                owner=user, title=title, visibility=default_visibility
            )
        tag.append_item(item)

    @staticmethod
    def get_manager_for_user(user):
        return TagManager(user)

    def __init__(self, user):
        self.owner = user

    @property
    def all_tags(self):
        return TagManager.all_tags_for_user(self.owner)

    @property
    def public_tags(self):
        return TagManager.all_tags_for_user(self.owner, public_only=True)

    def add_item_tags(self, item, tags, visibility=0):
        for tag in tags:
            TagManager.add_tag_by_user(item, tag, self.owner, visibility)

    def get_item_tags(self, item):
        return sorted(
            [
                m["parent__title"]
                for m in TagMember.objects.filter(
                    parent__owner=self.owner, item=item
                ).values("parent__title")
            ]
        )


Item.tags = property(TagManager.indexable_tags_for_item)
User.tags = property(TagManager.all_tags_for_user)
User.tag_manager = cached_property(TagManager.get_manager_for_user)
User.tag_manager.__set_name__(User, "tag_manager")


class Mark:
    """
    Holding Mark for an item on an shelf,
    which is a combo object of ShelfMember, Comment, Rating and Tags.
    it mimics previous mark behaviour.
    """

    def __init__(self, user, item):
        self.owner = user
        self.item = item

    @cached_property
    def shelfmember(self):
        return self.owner.shelf_manager.locate_item(self.item)

    @property
    def id(self):
        return self.shelfmember.id if self.shelfmember else None

    @cached_property
    def shelf(self):
        return self.shelfmember.parent if self.shelfmember else None

    @property
    def shelf_type(self):
        return self.shelfmember.parent.shelf_type if self.shelfmember else None

    @property
    def action_label(self):
        if self.shelfmember:
            return ShelfManager.get_action_label(self.shelf_type, self.item.category)
        if self.comment:
            return ShelfManager.get_action_label(
                ShelfType.PROGRESS, self.comment.item.category
            )
        return ""

    @property
    def shelf_label(self):
        return (
            ShelfManager.get_label(self.shelf_type, self.item.category)
            if self.shelfmember
            else None
        )

    @property
    def created_time(self):
        return self.shelfmember.created_time if self.shelfmember else None

    @property
    def metadata(self):
        return self.shelfmember.metadata if self.shelfmember else None

    @property
    def visibility(self):
        return (
            self.shelfmember.visibility
            if self.shelfmember
            else self.owner.get_preference().default_visibility
        )

    @cached_property
    def tags(self):
        return self.owner.tag_manager.get_item_tags(self.item)

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating_by_user(self.item, self.owner)

    @cached_property
    def comment(self):
        return Comment.objects.filter(
            owner=self.owner, item=self.item, focus_item__isnull=True
        ).first()

    @property
    def comment_text(self):
        return (self.comment.text or None) if self.comment else None

    @property
    def comment_html(self):
        return self.comment.html if self.comment else None

    @cached_property
    def review(self):
        return Review.objects.filter(owner=self.owner, item=self.item).first()

    def update(
        self,
        shelf_type,
        comment_text,
        rating_grade,
        visibility,
        metadata=None,
        created_time=None,
        share_to_mastodon=False,
    ):
        share = (
            share_to_mastodon
            and shelf_type is not None
            and (
                shelf_type != self.shelf_type
                or comment_text != self.comment_text
                or rating_grade != self.rating_grade
            )
        )
        if created_time and created_time >= timezone.now():
            created_time = None
        share_as_new_post = shelf_type != self.shelf_type
        original_visibility = self.visibility
        if shelf_type != self.shelf_type or visibility != original_visibility:
            self.shelfmember = self.owner.shelf_manager.move_item(
                self.item, shelf_type, visibility=visibility, metadata=metadata
            )
        if self.shelfmember and created_time:
            # if it's an update(not delete) and created_time is specified,
            # update the timestamp of the shelfmember and log
            log = ShelfLogEntry.objects.filter(
                owner=self.owner,
                item=self.item,
                timestamp=self.shelfmember.created_time,
            ).first()
            self.shelfmember.created_time = created_time
            self.shelfmember.save(update_fields=["created_time"])
            if log:
                log.timestamp = created_time
                log.save(update_fields=["timestamp"])
            else:
                ShelfLogEntry.objects.create(
                    owner=self.owner,
                    shelf_type=shelf_type,
                    item=self.item,
                    metadata=self.metadata,
                    timestamp=created_time,
                )
        if comment_text != self.comment_text or visibility != original_visibility:
            self.comment = Comment.comment_item_by_user(
                self.item,
                self.owner,
                comment_text,
                visibility,
                self.shelfmember.created_time if self.shelfmember else None,
            )
        if rating_grade != self.rating_grade or visibility != original_visibility:
            Rating.rate_item_by_user(self.item, self.owner, rating_grade, visibility)
            self.rating_grade = rating_grade
        if share:
            # this is a bit hacky but let's keep it until move to implement ActivityPub,
            # by then, we'll just change this to boost
            from mastodon.api import share_mark

            self.shared_link = (
                self.shelfmember.metadata.get("shared_link")
                if self.shelfmember.metadata and not share_as_new_post
                else None
            )
            self.save = lambda **args: None
            if not share_mark(self):
                raise ValueError("sharing failed")
            if self.shelfmember.metadata.get("shared_link") != self.shared_link:
                self.shelfmember.metadata["shared_link"] = self.shared_link
                self.shelfmember.save()
        elif share_as_new_post and self.shelfmember:
            self.shelfmember.metadata["shared_link"] = None
            self.shelfmember.save()

    def delete(self):
        self.update(None, None, None, 0)

    @property
    def logs(self):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=self.item).order_by(
            "timestamp"
        )


def reset_journal_visibility_for_user(user: User, visibility: int):
    ShelfMember.objects.filter(owner=user).update(visibility=visibility)
    Comment.objects.filter(owner=user).update(visibility=visibility)
    Rating.objects.filter(owner=user).update(visibility=visibility)
    Review.objects.filter(owner=user).update(visibility=visibility)


def remove_data_by_user(user: User):
    ShelfMember.objects.filter(owner=user).delete()
    Comment.objects.filter(owner=user).delete()
    Rating.objects.filter(owner=user).delete()
    Review.objects.filter(owner=user).delete()
    TagMember.objects.filter(owner=user).delete()
    Tag.objects.filter(owner=user).delete()
    CollectionMember.objects.filter(owner=user).delete()
    Collection.objects.filter(owner=user).delete()
    FeaturedCollection.objects.filter(owner=user).delete()


def update_journal_for_merged_item(legacy_item_uuid, delete_duplicated=False):
    legacy_item = Item.get_by_url(legacy_item_uuid)
    if not legacy_item:
        _logger.error("update_journal_for_merged_item: unable to find item")
        return
    new_item = legacy_item.merged_to_item
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        for p in cls.objects.filter(item=legacy_item):
            try:
                p.item = new_item
                p.save(update_fields=["item_id"])
            except:
                if delete_duplicated:
                    _logger.warn(
                        f"deleted piece {p} when merging {cls.__name__}: {legacy_item} -> {new_item}"
                    )
                    p.delete()
                else:
                    _logger.warn(
                        f"skip piece {p} when merging {cls.__name__}: {legacy_item} -> {new_item}"
                    )


def journal_exists_for_item(item):
    for cls in list(Content.__subclasses__()) + list(ListMember.__subclasses__()):
        if cls.objects.filter(item=item).exists():
            return True
    return False


Item.journal_exists = journal_exists_for_item
