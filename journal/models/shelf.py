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
    WISHLIST = ("wishlist", _("WISHLIST"))
    PROGRESS = ("progress", _("PROGRESS"))
    COMPLETE = ("complete", _("COMPLETE"))
    DROPPED = ("dropped", _("DROPPED"))


_REVIEWED = "reviewed"

_SHELF_LABELS = [
    [
        ItemCategory.Book,
        ShelfType.WISHLIST,
        _("books to read"),
        _("want to read"),
        _("wants to read {item}"),
    ],
    [
        ItemCategory.Book,
        ShelfType.PROGRESS,
        _("books reading"),
        _("start reading"),
        _("started reading {item}"),
    ],
    [
        ItemCategory.Book,
        ShelfType.COMPLETE,
        _("books completed"),
        _("finish reading"),
        _("finished reading {item}"),
    ],
    [
        ItemCategory.Book,
        ShelfType.DROPPED,
        _("books dropped"),
        _("stop reading"),
        _("stopped reading {item}"),
    ],
    [
        ItemCategory.Book,
        _REVIEWED,
        _("books reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.WISHLIST,
        _("movies to watch"),
        _("want to watch"),
        _("wants to watch {item}"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.PROGRESS,
        _("movies watching"),
        _("start watching"),
        _("started watching {item}"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.COMPLETE,
        _("movies watched"),
        _("finish watching"),
        _("finished watching {item}"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.DROPPED,
        _("movies dropped"),
        _("stop watching"),
        _("stopped watching {item}"),
    ],
    [
        ItemCategory.Movie,
        _REVIEWED,
        _("movies reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.TV,
        ShelfType.WISHLIST,
        _("TV shows to watch"),
        _("want to watch"),
        _("wants to watch {item}"),
    ],
    [
        ItemCategory.TV,
        ShelfType.PROGRESS,
        _("TV shows watching"),
        _("start watching"),
        _("started watching {item}"),
    ],
    [
        ItemCategory.TV,
        ShelfType.COMPLETE,
        _("TV shows watched"),
        _("finish watching"),
        _("finished watching {item}"),
    ],
    [
        ItemCategory.TV,
        ShelfType.DROPPED,
        _("TV shows dropped"),
        _("stop watching"),
        _("stopped watching {item}"),
    ],
    [
        ItemCategory.TV,
        _REVIEWED,
        _("TV shows reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.Music,
        ShelfType.WISHLIST,
        _("albums to listen"),
        _("want to listen"),
        _("wants to listen {item}"),
    ],
    [
        ItemCategory.Music,
        ShelfType.PROGRESS,
        _("albums listening"),
        _("start listening"),
        _("started listening {item}"),
    ],
    [
        ItemCategory.Music,
        ShelfType.COMPLETE,
        _("albums listened"),
        _("finish listening"),
        _("finished listening {item}"),
    ],
    [
        ItemCategory.Music,
        ShelfType.DROPPED,
        _("albums dropped"),
        _("stop listening"),
        _("stopped listening {item}"),
    ],
    [
        ItemCategory.Music,
        _REVIEWED,
        _("albums reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.Game,
        ShelfType.WISHLIST,
        _("games to play"),
        _("want to play"),
        _("wants to play {item}"),
    ],
    [
        ItemCategory.Game,
        ShelfType.PROGRESS,
        _("games playing"),
        _("start playing"),
        _("started playing {item}"),
    ],
    [
        ItemCategory.Game,
        ShelfType.COMPLETE,
        _("games played"),
        _("finish playing"),
        _("finished playing {item}"),
    ],
    [
        ItemCategory.Game,
        ShelfType.DROPPED,
        _("games dropped"),
        _("stop playing"),
        _("stopped playing {item}"),
    ],
    [
        ItemCategory.Game,
        _REVIEWED,
        _("games reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.WISHLIST,
        _("podcasts to listen"),
        _("want to listen"),
        _("wants to listen {item}"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.PROGRESS,
        _("podcasts listening"),
        _("start listening"),
        _("started listening {item}"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.COMPLETE,
        _("podcasts listened"),
        _("finish listening"),
        _("finished listening {item}"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.DROPPED,
        _("podcasts dropped"),
        _("stop listening"),
        _("stopped listening {item}"),
    ],
    [
        ItemCategory.Podcast,
        _REVIEWED,
        _("podcasts reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
    [
        ItemCategory.Performance,
        ShelfType.WISHLIST,
        _("performances to see"),
        _("want to see"),
        _("wants to see {item}"),
    ],
    # disable progress shelf for Performance
    [ItemCategory.Performance, ShelfType.PROGRESS, "", "", ""],
    [
        ItemCategory.Performance,
        ShelfType.COMPLETE,
        _("performances saw"),
        _("finish seeing"),
        _("finished seeing {item}"),
    ],
    [
        ItemCategory.Performance,
        ShelfType.DROPPED,
        _("performances dropped"),
        _("stop seeing"),
        _("stopped seeing {item}"),
    ],
    [
        ItemCategory.Performance,
        _REVIEWED,
        _("performances reviewed"),
        _("review"),
        _("wrote a review of {item}"),
    ],
]
# grammatically problematic, for translation only


class ShelfMember(ListMember):
    if TYPE_CHECKING:
        parent: models.ForeignKey["ShelfMember", "Shelf"]

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

    if TYPE_CHECKING:
        members: models.QuerySet[ShelfMember]

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
            return _("removed mark")

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
    def get_labels_for_category(cls, item_category: ItemCategory):
        return [(n[1], n[2]) for n in _SHELF_LABELS if n[0] == item_category]

    @classmethod
    def get_actions_for_category(cls, item_category: ItemCategory):
        return [
            (n[1], n[3])
            for n in _SHELF_LABELS
            if n[0] == item_category and n[1] != _REVIEWED
        ]

    @classmethod
    def get_label(cls, shelf_type: ShelfType | str, item_category: ItemCategory) -> str:
        st = str(shelf_type)
        sts = [n[2] for n in _SHELF_LABELS if n[0] == item_category and n[1] == st]
        return sts[0] if sts else st

    @classmethod
    def get_action_label(
        cls, shelf_type: ShelfType | str, item_category: ItemCategory
    ) -> str:
        st = str(shelf_type)
        sts = [n[3] for n in _SHELF_LABELS if n[0] == item_category and n[1] == st]
        return sts[0] if sts else st

    @classmethod
    def get_action_template(
        cls, shelf_type: ShelfType | str, item_category: ItemCategory
    ) -> str:
        st = str(shelf_type)
        sts = [n[4] for n in _SHELF_LABELS if n[0] == item_category and n[1] == st]
        return sts[0] if sts else st

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
                        typ = "other"
                    if typ not in calendar_data[date]["items"]:
                        calendar_data[date]["items"].append(typ)
        return calendar_data
