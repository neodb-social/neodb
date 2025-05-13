from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import connection, models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from polymorphic.models import ContentType, PolymorphicManager

from catalog.common.models import item_categories
from catalog.models import Item, ItemCategory
from takahe.utils import Takahe
from users.models import APIdentity

from .common import q_item_in_category
from .itemlist import List, ListMember
from .renderers import render_post_with_macro, render_rating, render_spoiler_text

if TYPE_CHECKING:
    from .comment import Comment
    from .mark import Mark
    from .rating import Rating


class ShelfType(models.TextChoices):
    WISHLIST = "wishlist", _("WISHLIST")
    PROGRESS = "progress", _("PROGRESS")
    COMPLETE = "complete", _("COMPLETE")
    DROPPED = "dropped", _("DROPPED")


_REVIEWED = "reviewed"

_SHELF_LABELS = [
    [
        ItemCategory.Book,
        ShelfType.WISHLIST,
        _("books to read"),  # shelf label
        _("want to read"),  # action label
        _("wants to read {item}"),  # feed
        _("to read"),  # status label
    ],
    [
        ItemCategory.Book,
        ShelfType.PROGRESS,
        _("books reading"),
        _("start reading"),
        _("started reading {item}"),
        _("reading"),
    ],
    [
        ItemCategory.Book,
        ShelfType.COMPLETE,
        _("books completed"),
        _("finish reading"),
        _("finished reading {item}"),
        _("read"),
    ],
    [
        ItemCategory.Book,
        ShelfType.DROPPED,
        _("books dropped"),
        _("stop reading"),
        _("stopped reading {item}"),
        _("stopped reading"),
    ],
    [
        ItemCategory.Book,
        _REVIEWED,
        _("books reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.Movie,
        ShelfType.WISHLIST,
        _("movies to watch"),
        _("want to watch"),
        _("wants to watch {item}"),
        _("to watch"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.PROGRESS,
        _("movies watching"),
        _("start watching"),
        _("started watching {item}"),
        _("watching"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.COMPLETE,
        _("movies watched"),
        _("finish watching"),
        _("finished watching {item}"),
        _("watched"),
    ],
    [
        ItemCategory.Movie,
        ShelfType.DROPPED,
        _("movies dropped"),
        _("stop watching"),
        _("stopped watching {item}"),
        _("stopped watching"),
    ],
    [
        ItemCategory.Movie,
        _REVIEWED,
        _("movies reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.TV,
        ShelfType.WISHLIST,
        _("TV shows to watch"),
        _("want to watch"),
        _("wants to watch {item}"),
        _("to watch"),
    ],
    [
        ItemCategory.TV,
        ShelfType.PROGRESS,
        _("TV shows watching"),
        _("start watching"),
        _("started watching {item}"),
        _("watching"),
    ],
    [
        ItemCategory.TV,
        ShelfType.COMPLETE,
        _("TV shows watched"),
        _("finish watching"),
        _("finished watching {item}"),
        _("watched"),
    ],
    [
        ItemCategory.TV,
        ShelfType.DROPPED,
        _("TV shows dropped"),
        _("stop watching"),
        _("stopped watching {item}"),
        _("stopped watching"),
    ],
    [
        ItemCategory.TV,
        _REVIEWED,
        _("TV shows reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.Music,
        ShelfType.WISHLIST,
        _("albums to listen"),
        _("want to listen"),
        _("wants to listen {item}"),
        _("to listen"),
    ],
    [
        ItemCategory.Music,
        ShelfType.PROGRESS,
        _("albums listening"),
        _("start listening"),
        _("started listening {item}"),
        _("listening"),
    ],
    [
        ItemCategory.Music,
        ShelfType.COMPLETE,
        _("albums listened"),
        _("finish listening"),
        _("finished listening {item}"),
        _("listened"),
    ],
    [
        ItemCategory.Music,
        ShelfType.DROPPED,
        _("albums dropped"),
        _("stop listening"),
        _("stopped listening {item}"),
        _("stopped listening"),
    ],
    [
        ItemCategory.Music,
        _REVIEWED,
        _("albums reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.Game,
        ShelfType.WISHLIST,
        _("games to play"),
        _("want to play"),
        _("wants to play {item}"),
        _("to play"),
    ],
    [
        ItemCategory.Game,
        ShelfType.PROGRESS,
        _("games playing"),
        _("start playing"),
        _("started playing {item}"),
        _("playing"),
    ],
    [
        ItemCategory.Game,
        ShelfType.COMPLETE,
        _("games played"),
        _("finish playing"),
        _("finished playing {item}"),
        _("played"),
    ],
    [
        ItemCategory.Game,
        ShelfType.DROPPED,
        _("games dropped"),
        _("stop playing"),
        _("stopped playing {item}"),
        _("stopped playing"),
    ],
    [
        ItemCategory.Game,
        _REVIEWED,
        _("games reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.Podcast,
        ShelfType.WISHLIST,
        _("podcasts to listen"),
        _("want to listen"),
        _("wants to listen {item}"),
        _("to listen"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.PROGRESS,
        _("podcasts listening"),
        _("start listening"),
        _("started listening {item}"),
        _("listening"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.COMPLETE,
        _("podcasts listened"),
        _("finish listening"),
        _("finished listening {item}"),
        _("listened"),
    ],
    [
        ItemCategory.Podcast,
        ShelfType.DROPPED,
        _("podcasts dropped"),
        _("stop listening"),
        _("stopped listening {item}"),
        _("stopped listening"),
    ],
    [
        ItemCategory.Podcast,
        _REVIEWED,
        _("podcasts reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
    [
        ItemCategory.Performance,
        ShelfType.WISHLIST,
        _("performances to see"),
        _("want to see"),
        _("wants to see {item}"),
        _("to see"),
    ],
    # disable progress shelf for Performance
    [ItemCategory.Performance, ShelfType.PROGRESS, "", "", "", ""],
    [
        ItemCategory.Performance,
        ShelfType.COMPLETE,
        _("performances saw"),
        _("finish seeing"),
        _("finished seeing {item}"),
        _("seen"),
    ],
    [
        ItemCategory.Performance,
        ShelfType.DROPPED,
        _("performances dropped"),
        _("stop seeing"),
        _("stopped seeing {item}"),
        _("stopped seeing"),
    ],
    [
        ItemCategory.Performance,
        _REVIEWED,
        _("performances reviewed"),
        _("review"),
        _("wrote a review of {item}"),
        "",
    ],
]
# grammatically problematic, for translation only


class ShelfMemberManager(PolymorphicManager):
    def get_queryset(self):
        from .comment import Comment
        from .rating import Rating

        rating_subquery = Rating.objects.filter(
            owner_id=models.OuterRef("owner_id"), item_id=models.OuterRef("item_id")
        ).values("grade")[:1]
        comment_subquery = Comment.objects.filter(
            owner_id=models.OuterRef("owner_id"), item_id=models.OuterRef("item_id")
        ).values("text")[:1]
        return (
            super()
            .get_queryset()
            .annotate(
                _rating_grade=models.Subquery(rating_subquery),
                _comment_text=models.Subquery(comment_subquery),
                _shelf_type=models.F("parent__shelf_type"),
            )
        )


class ShelfMember(ListMember):
    if TYPE_CHECKING:
        parent: models.ForeignKey["ShelfMember", "Shelf"]

    parent = models.ForeignKey(
        "Shelf", related_name="members", on_delete=models.CASCADE
    )

    objects = ShelfMemberManager()

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
        cls, owner: APIdentity, item: Item, obj: dict, post, crosspost=None
    ):
        if post.local:  # ignore local user updating their post via Mastodon API
            return
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
            "visibility": Takahe.visibility_t2n(post.visibility),
            "created_time": datetime.fromisoformat(obj["published"]),
            "edited_time": datetime.fromisoformat(obj["updated"]),
        }
        p, _ = cls.objects.update_or_create(owner=owner, item=item, defaults=d)
        p.link_post_id(post.id)
        return p

    def get_crosspost_postfix(self):
        tags = render_post_with_macro(
            self.owner.user.preference.mastodon_append_tag, self.item
        )
        return "\n" + tags if tags else ""

    def get_crosspost_template(self):
        return _(ShelfManager.get_action_template(self.shelf_type, self.item.category))

    def to_crosspost_params(self):
        action = self.get_crosspost_template().format(item="##obj##")
        if self.sibling_comment:
            spoiler, txt = render_spoiler_text(self.sibling_comment.text, self.item)
        else:
            spoiler, txt = None, ""
        rating = self.sibling_rating.grade if self.sibling_rating else ""
        txt = "\n" + txt if txt else ""
        content = f"{action} ##rating## \n##obj_link_if_plain##{txt}{self.get_crosspost_postfix()}"
        params = {
            "content": content,
            "spoiler_text": spoiler,
            "obj": self.item,
            "rating": rating,
        }
        return params

    def to_post_params(self):
        item_link = f"{settings.SITE_INFO['site_url']}/~neodb~{self.item.url}"
        action = self.get_crosspost_template().format(
            item=f'<a href="{item_link}">{self.item.display_title}</a>'
        )
        if self.sibling_comment:
            spoiler, txt = render_spoiler_text(self.sibling_comment.text, self.item)
        else:
            spoiler, txt = None, ""
        postfix = self.get_crosspost_postfix()
        # add @user.mastodon.handle so that user can see it on Mastodon ?
        # if self.visibility and self.owner.user.mastodon:
        #     postfix += f" @{self.owner.user.mastodon.handle}"
        content = f"{render_rating(self.sibling_rating.grade) if self.sibling_rating else ''} \n{txt}\n{postfix}"
        return {
            "prepend_content": action,
            "content": content,
            "summary": spoiler,
            "sensitive": spoiler is not None,
        }

    def get_ap_data(self):
        data = super().get_ap_data()
        if self.sibling_comment:
            data["object"]["relatedWith"].append(self.sibling_comment.ap_object)
        if self.sibling_rating:
            data["object"]["relatedWith"].append(self.sibling_rating.ap_object)
        return data

    def sync_to_timeline(self, update_mode: int = 0):
        post = super().sync_to_timeline(update_mode)
        if post and self.sibling_comment:
            self.sibling_comment.link_post_id(post.id)
        return post

    def to_indexable_doc(self) -> dict[str, Any]:
        from .tag import Tag

        ids = [self.pk]
        classes = [self.__class__.__name__]
        content = []
        rating = 0
        if self.sibling_rating:
            # ids.append(self.sibling_rating.pk)
            classes.append("Rating")
            rating = self.sibling_rating.grade
        if self.sibling_comment:
            # ids.append(self.sibling_comment.pk)
            classes.append("Comment")
            content = [self.sibling_comment.text]
        return {
            "piece_id": ids,
            "piece_class": classes,
            "item_id": [self.item.id],
            "item_class": [self.item.__class__.__name__],
            "item_title": self.item.to_indexable_titles(),
            "shelf_type": self.shelf_type,
            "rating": rating,
            "tag": [Tag.deep_cleanup_title(t, replacement="_") for t in self.tags],
            "content": content,
        }

    def save(self, *args, **kwargs):
        try:
            del self._shelf_type  # type:ignore
            del self._rating_grade  # type:ignore
            del self._comment_text  # type:ignore
        except AttributeError:
            pass
        return super().save(*args, **kwargs)

    @cached_property
    def sibling_comment(self) -> "Comment | None":
        from .comment import Comment

        return Comment.objects.filter(owner=self.owner, item=self.item).first()

    @cached_property
    def sibling_rating(self) -> "Rating | None":
        from .rating import Rating

        return Rating.objects.filter(owner=self.owner, item=self.item).first()

    @cached_property
    def mark(self) -> "Mark":
        from .mark import Mark

        m = Mark(self.owner, self.item)
        m.shelfmember = self
        return m

    @property
    def shelf_label(self) -> str | None:
        return ShelfManager.get_label(self.shelf_type, self.item.category)

    @property
    def shelf_type(self):
        try:
            return getattr(self, "_shelf_type")
        except AttributeError:
            return self.parent.shelf_type

    @property
    def rating_grade(self):
        try:
            return getattr(self, "_rating_grade")
        except AttributeError:
            return self.mark.rating_grade

    @property
    def comment_text(self):
        try:
            return getattr(self, "_comment_text")
        except AttributeError:
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

    def to_indexable_doc(self) -> dict[str, Any]:
        return {}


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

    def all_post_ids(self):
        return ShelfLogEntryPost.objects.filter(log_entry=self).values_list(
            "post_id", flat=True
        )


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
    def get_statuses_for_category(cls, item_category: ItemCategory):
        return [
            (n[1], n[5])
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
    def get_status_label(
        cls, shelf_type: ShelfType | str, item_category: ItemCategory
    ) -> str:
        st = str(shelf_type)
        sts = [n[5] for n in _SHELF_LABELS if n[0] == item_category and n[1] == st]
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

    def get_stats(self, q: models.Q | None = None) -> dict:
        from .review import Review

        if not q:
            q = models.Q(owner=self.owner)
        qs = (
            ShelfMember.objects.filter(q)
            .values("parent__shelf_type", "item__polymorphic_ctype_id")
            .annotate(num=models.Count("item"))
        )
        qs2 = (
            Review.objects.filter(q)
            .values("item__polymorphic_ctype_id")
            .annotate(num=models.Count("item"))
        )
        stats = {
            cat: {t: 0 for t in (ShelfType.values + ["reviewed"])}
            for cat in ItemCategory.values
        }
        for cat, item_classes in item_categories().items():
            ct_ids = [
                ContentType.objects.get_for_model(item_cls).pk
                for item_cls in item_classes
            ]
            for typ in ShelfType.values:
                stats[cat][typ] = sum(
                    [
                        s["num"]
                        for s in qs
                        if s["item__polymorphic_ctype_id"] in ct_ids
                        and s["parent__shelf_type"] == typ
                    ],
                    0,
                )
            stats[cat]["reviewed"] = sum(
                [s["num"] for s in qs2 if s["item__polymorphic_ctype_id"] in ct_ids], 0
            )
        return stats

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
                "SELECT to_char(DATE(journal_comment.created_time::timestamp AT TIME ZONE %s), 'YYYY-MM-DD') AS dat, django_content_type.model typ, COUNT(1) count FROM journal_comment, catalog_item, django_content_type WHERE journal_comment.owner_id = %s AND journal_comment.item_id = catalog_item.id AND django_content_type.id = catalog_item.polymorphic_ctype_id AND journal_comment.created_time >= NOW() - INTERVAL '366 days' AND journal_comment.visibility <= %s AND django_content_type.model in ('tvepisode', 'podcastepisode') GROUP BY item_id, dat, typ;",
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
