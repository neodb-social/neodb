from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING

from django.db import connection, models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import Item, ItemCategory
from users.models import APIdentity

from .common import q_item_in_category
from .itemlist import List, ListMember

if TYPE_CHECKING:
    from .mark import Mark


class ShelfType(models.TextChoices):
    WISHLIST = ("wishlist", "未开始")
    PROGRESS = ("progress", "进行中")
    COMPLETE = ("complete", "完成")
    DROPPED = ("dropped", "放弃")


SHELF_LABELS = [
    [ItemCategory.Book, ShelfType.WISHLIST, _("想读")],
    [ItemCategory.Book, ShelfType.PROGRESS, _("在读")],
    [ItemCategory.Book, ShelfType.COMPLETE, _("读过")],
    [ItemCategory.Book, ShelfType.DROPPED, _("不读了")],
    [ItemCategory.Movie, ShelfType.WISHLIST, _("想看")],
    [ItemCategory.Movie, ShelfType.PROGRESS, _("在看")],
    [ItemCategory.Movie, ShelfType.COMPLETE, _("看过")],
    [ItemCategory.Movie, ShelfType.DROPPED, _("不看了")],
    [ItemCategory.TV, ShelfType.WISHLIST, _("想看")],
    [ItemCategory.TV, ShelfType.PROGRESS, _("在看")],
    [ItemCategory.TV, ShelfType.COMPLETE, _("看过")],
    [ItemCategory.TV, ShelfType.DROPPED, _("不看了")],
    [ItemCategory.Music, ShelfType.WISHLIST, _("想听")],
    [ItemCategory.Music, ShelfType.PROGRESS, _("在听")],
    [ItemCategory.Music, ShelfType.COMPLETE, _("听过")],
    [ItemCategory.Music, ShelfType.DROPPED, _("不听了")],
    [ItemCategory.Game, ShelfType.WISHLIST, _("想玩")],
    [ItemCategory.Game, ShelfType.PROGRESS, _("在玩")],
    [ItemCategory.Game, ShelfType.COMPLETE, _("玩过")],
    [ItemCategory.Game, ShelfType.DROPPED, _("不玩了")],
    [ItemCategory.Podcast, ShelfType.WISHLIST, _("想听")],
    [ItemCategory.Podcast, ShelfType.PROGRESS, _("在听")],
    [ItemCategory.Podcast, ShelfType.COMPLETE, _("听过")],
    [ItemCategory.Podcast, ShelfType.DROPPED, _("不听了")],
    # disable all shelves for PodcastEpisode
    [ItemCategory.Performance, ShelfType.WISHLIST, _("想看")],
    # disable progress shelf for Performance
    [ItemCategory.Performance, ShelfType.PROGRESS, _("")],
    [ItemCategory.Performance, ShelfType.COMPLETE, _("看过")],
    [ItemCategory.Performance, ShelfType.DROPPED, _("不看了")],
]


def get_shelf_labels_for_category(item_category: ItemCategory):
    return [(n[1], n[2]) for n in SHELF_LABELS if n[0] == item_category]


class ShelfMember(ListMember):
    parent = models.ForeignKey(
        "Shelf", related_name="members", on_delete=models.CASCADE
    )

    class Meta:
        unique_together = [["owner", "item"]]
        indexes = [
            models.Index(fields=["parent_id", "visibility", "created_time"]),
        ]

    @property
    def ap_object(self):
        return {
            "id": self.absolute_url,
            "type": "Status",
            "status": self.parent.shelf_type,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "href": self.absolute_url,
        }

    @classmethod
    def update_by_ap_object(
        cls, owner: APIdentity, item: Item, obj: dict, post_id: int, visibility: int
    ):
        p = cls.objects.filter(owner=owner, item=item).first()
        if p and p.edited_time >= datetime.fromisoformat(obj["updated"]):
            return p  # incoming ap object is older than what we have, no update needed
        shelf = owner.shelf_manager.get_shelf(obj["status"])
        if not shelf:
            logger.warning(f"unable to locate shelf for {owner}, {obj}")
            return
        d = {
            "parent": shelf,
            "position": 0,
            "local": False,
            "visibility": visibility,
            "created_time": datetime.fromisoformat(obj["published"]),
            "edited_time": datetime.fromisoformat(obj["updated"]),
        }
        p, _ = cls.objects.update_or_create(owner=owner, item=item, defaults=d)
        p.link_post_id(post_id)
        return p

    @cached_property
    def mark(self) -> "Mark":
        from .mark import Mark

        m = Mark(self.owner, self.item)
        m.shelfmember = self
        return m

    @property
    def shelf_label(self) -> str | None:
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

    def ensure_log_entry(self):
        log, _ = ShelfLogEntry.objects.get_or_create(
            owner=self.owner,
            shelf_type=self.shelf_type,
            item=self.item,
            timestamp=self.created_time,
        )
        return log

    def log_and_delete(self):
        ShelfLogEntry.objects.get_or_create(
            owner=self.owner,
            shelf_type=None,
            item=self.item,
            timestamp=timezone.now(),
        )
        self.delete()

    def link_post_id(self, post_id: int):
        if self.local:
            self.ensure_log_entry().link_post_id(post_id)
        return super().link_post_id(post_id)


class Shelf(List):
    """
    Shelf
    """

    class Meta:
        unique_together = [["owner", "shelf_type"]]

    MEMBER_CLASS = ShelfMember
    items = models.ManyToManyField(Item, through="ShelfMember", related_name="+")
    shelf_type = models.CharField(
        choices=ShelfType.choices, max_length=100, null=False, blank=False
    )

    def __str__(self):
        return f"Shelf:{self.owner.username}:{self.shelf_type}"


class ShelfLogEntry(models.Model):
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    shelf_type = models.CharField(choices=ShelfType.choices, max_length=100, null=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    timestamp = models.DateTimeField()  # this may later be changed by user
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)
    posts = models.ManyToManyField(
        "takahe.Post", related_name="log_entries", through="ShelfLogEntryPost"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "item", "timestamp", "shelf_type"],
                name="unique_shelf_log_entry",
            ),
        ]

    def __str__(self):
        return f"LOG:{self.owner}:{self.shelf_type}:{self.item.uuid}:{self.timestamp}"

    @property
    def action_label(self):
        if self.shelf_type:
            return ShelfManager.get_action_label(self.shelf_type, self.item.category)
        else:
            return _("移除标记")

    def link_post_id(self, post_id: int):
        ShelfLogEntryPost.objects.get_or_create(log_entry=self, post_id=post_id)


class ShelfLogEntryPost(models.Model):
    log_entry = models.ForeignKey(ShelfLogEntry, on_delete=models.CASCADE)
    post = models.ForeignKey(
        "takahe.Post", db_constraint=False, db_index=True, on_delete=models.CASCADE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["log_entry", "post"], name="unique_log_entry_post"
            ),
        ]


class ShelfManager:
    """
    ShelfManager

    all shelf operations should go thru this class so that ShelfLogEntry can be properly populated
    ShelfLogEntry can later be modified if user wish to change history
    """

    def __init__(self, owner):
        self.owner = owner
        qs = Shelf.objects.filter(owner=self.owner)
        self.shelf_list = {v.shelf_type: v for v in qs}
        if len(self.shelf_list) < len(ShelfType):
            self.initialize()

    def initialize(self):
        for qt in ShelfType:
            self.shelf_list[qt] = Shelf.objects.get_or_create(
                owner=self.owner, shelf_type=qt
            )[0]

    def locate_item(self, item: Item) -> ShelfMember | None:
        return ShelfMember.objects.filter(item=item, owner=self.owner).first()

    def get_log_for_item(self, item: Item):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=item).order_by(
            "timestamp"
        )

    def get_shelf(self, shelf_type: ShelfType):
        return self.shelf_list[shelf_type]

    def get_latest_members(
        self, shelf_type: ShelfType, item_category: ItemCategory | None = None
    ):
        qs = self.shelf_list[shelf_type].members.all().order_by("-created_time")
        if item_category:
            return qs.filter(q_item_in_category(item_category))
        else:
            return qs

    def get_members(
        self, shelf_type: ShelfType, item_category: ItemCategory | None = None
    ):
        qs = self.shelf_list[shelf_type].members.all()
        if item_category:
            return qs.filter(q_item_in_category(item_category))
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
    def get_action_label(
        cls, shelf_type: ShelfType | str, item_category: ItemCategory
    ) -> str:
        st = str(shelf_type)
        sts = [n[2] for n in SHELF_LABELS if n[0] == item_category and n[1] == st]
        return sts[0] if sts else st

    @classmethod
    def get_label(cls, shelf_type: ShelfType, item_category: ItemCategory):
        ic = ItemCategory(item_category).label
        st = cls.get_action_label(shelf_type, item_category)
        return (
            _("{shelf_label}的{item_category}").format(shelf_label=st, item_category=ic)
            if st
            else None
        )

    @staticmethod
    def get_manager_for_user(owner: APIdentity):
        return ShelfManager(owner)

    def get_calendar_data(self, max_visiblity: int):
        shelf_id = self.get_shelf(ShelfType.COMPLETE).pk
        timezone_offset = timezone.localtime(timezone.now()).strftime("%z")
        timezone_offset = timezone_offset[: len(timezone_offset) - 2]
        calendar_data = {}
        queries = [
            (
                "SELECT to_char(DATE(journal_shelfmember.created_time::timestamp AT TIME ZONE %s), 'YYYY-MM-DD') AS dat, django_content_type.model typ, COUNT(1) count FROM journal_shelfmember, catalog_item, django_content_type WHERE journal_shelfmember.item_id = catalog_item.id AND django_content_type.id = catalog_item.polymorphic_ctype_id AND parent_id = %s AND journal_shelfmember.created_time >= NOW() - INTERVAL '366 days' AND journal_shelfmember.visibility <= %s GROUP BY item_id, dat, typ;",
                [timezone_offset, shelf_id, int(max_visiblity)],
            ),
            (
                "SELECT to_char(DATE(journal_comment.created_time::timestamp AT TIME ZONE %s), 'YYYY-MM-DD') AS dat, django_content_type.model typ, COUNT(1) count FROM journal_comment, catalog_item, django_content_type WHERE journal_comment.owner_id = %s AND journal_comment.item_id = catalog_item.id AND django_content_type.id = catalog_item.polymorphic_ctype_id AND journal_comment.created_time >= NOW() - INTERVAL '366 days' AND journal_comment.visibility <= %s GROUP BY item_id, dat, typ;",
                [timezone_offset, self.owner.id, int(max_visiblity)],
            ),
        ]
        for sql, params in queries:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                data = cursor.fetchall()
                for line in data:
                    date = line[0]
                    typ = line[1]
                    if date not in calendar_data:
                        calendar_data[date] = {"items": []}
                    if typ[:2] == "tv":
                        typ = "tv"
                    elif typ[:7] == "podcast":
                        typ = "podcast"
                    elif typ == "album":
                        typ = "music"
                    elif typ == "edition":
                        typ = "book"
                    elif typ not in [
                        "book",
                        "movie",
                        "tv",
                        "music",
                        "game",
                        "podcast",
                        "performance",
                    ]:
                        print(typ)
                        typ = "other"
                    if typ not in calendar_data[date]["items"]:
                        calendar_data[date]["items"].append(typ)
        return calendar_data
