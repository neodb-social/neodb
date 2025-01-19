from datetime import datetime
from typing import Any

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count

from catalog.models import Item
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Content

MIN_RATING_COUNT = 5
RATING_INCLUDES_CHILD_ITEMS = ["tvshow", "performance"]


class Rating(Content):
    class Meta:
        unique_together = [["owner", "item"]]

    grade = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(10), MinValueValidator(1)], null=True
    )

    @property
    def ap_object(self):
        return {
            "id": self.absolute_url,
            "type": "Rating",
            "best": 10,
            "worst": 1,
            "value": self.grade,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "href": self.absolute_url,
        }

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        p = cls.objects.filter(owner=owner, item=item).first()
        if p and p.edited_time >= datetime.fromisoformat(obj["updated"]):
            return p  # incoming ap object is older than what we have, no update needed
        value = obj.get("value", 0) if obj else 0
        if not value:
            cls.objects.filter(owner=owner, item=item).delete()
            return
        best = obj.get("best", 5)
        worst = obj.get("worst", 1)
        if best <= worst:
            return
        if value < worst:
            value = worst
        if value > best:
            value = best
        if best != 10 or worst != 1:
            value = round(9 * (value - worst) / (best - worst)) + 1
        else:
            value = round(value)
        d = {
            "grade": value,
            "local": False,
            "remote_id": obj["id"],
            "visibility": Takahe.visibility_t2n(post.visibility),
            "created_time": datetime.fromisoformat(obj["published"]),
            "edited_time": datetime.fromisoformat(obj["updated"]),
        }
        p = cls.objects.update_or_create(owner=owner, item=item, defaults=d)[0]
        p.link_post_id(post.id)
        return p

    @staticmethod
    def get_rating_for_item(item: Item) -> float | None:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(average=Avg("grade"), count=Count("item"))
        return round(stat["average"], 1) if stat["count"] >= MIN_RATING_COUNT else None

    @staticmethod
    def get_rating_count_for_item(item: Item) -> int:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(count=Count("item"))
        return stat["count"]

    @staticmethod
    def get_rating_distribution_for_item(item: Item):
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.values("grade").annotate(count=Count("grade")).order_by("grade")
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

    @staticmethod
    def update_item_rating(
        item: Item,
        owner: APIdentity,
        rating_grade: int | None,
        visibility: int = 0,
        created_time: datetime | None = None,
    ):
        if rating_grade and (rating_grade < 1 or rating_grade > 10):
            raise ValueError(f"Invalid rating grade: {rating_grade}")
        if not rating_grade:
            Rating.objects.filter(owner=owner, item=item).delete()
        else:
            d: dict[str, Any] = {"grade": rating_grade, "visibility": visibility}
            if created_time:
                d["created_time"] = created_time
            r, _ = Rating.objects.update_or_create(owner=owner, item=item, defaults=d)
            return r

    @staticmethod
    def get_item_rating(item: Item, owner: APIdentity) -> int | None:
        rating = Rating.objects.filter(owner=owner, item=item).first()
        return (rating.grade or None) if rating else None

    def to_indexable_doc(self) -> dict[str, Any]:
        # rating is not indexed individually but with shelfmember
        return {}
