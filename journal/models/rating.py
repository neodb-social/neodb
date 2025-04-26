from datetime import datetime
from typing import Any, Iterable

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count

from catalog.models import Item, Performance, TVShow
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Content

MIN_RATING_COUNT = 5
RATING_INCLUDES_CHILD_ITEMS = [TVShow, Performance]


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
        if post.local:  # ignore local user updating their post via Mastodon API
            return
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

    @classmethod
    def get_info_for_item(cls, item: Item) -> dict:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.__class__ in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.values("grade").annotate(count=Count("grade"))
        grades = [0] * 11
        votes = 0
        total = 0
        for s in stat:
            if s["grade"] and s["grade"] > 0 and s["grade"] < 11:
                grades[s["grade"]] = s["count"]
                total += s["count"] * s["grade"]
                votes += s["count"]
        if votes < MIN_RATING_COUNT:
            return {"average": None, "count": votes, "distribution": [0] * 5}
        else:
            return {
                "average": round(total / votes, 1),
                "count": votes,
                "distribution": [
                    100 * (grades[1] + grades[2]) // votes,
                    100 * (grades[3] + grades[4]) // votes,
                    100 * (grades[5] + grades[6]) // votes,
                    100 * (grades[7] + grades[8]) // votes,
                    100 * (grades[9] + grades[10]) // votes,
                ],
            }

    @classmethod
    def get_info_for_items(cls, items: Iterable[Item]) -> dict[int, dict]:
        # Extract IDs and build mapping for parent items
        item_ids = [item.pk for item in items]
        item_to_parent = {}
        all_item_ids = set(item_ids)

        # Handle items with child items and build mapping
        for item in items:
            if item.__class__ in RATING_INCLUDES_CHILD_ITEMS:
                for child_id in item.child_item_ids:
                    item_to_parent[child_id] = item.pk
                    all_item_ids.add(child_id)
            if item.pk not in item_to_parent:
                item_to_parent[item.pk] = item.pk

        # Get all ratings in a single query
        stat = (
            Rating.objects.filter(grade__isnull=False, item_id__in=all_item_ids)
            .values("item_id", "grade")
            .annotate(count=Count("grade"))
        )

        # Initialize counters
        grades = {item_id: [0] * 11 for item_id in item_ids}
        votes = {item_id: 0 for item_id in item_ids}
        total = {item_id: 0 for item_id in item_ids}

        # Process the ratings
        for s in stat:
            if s["grade"] and 0 < s["grade"] < 11:
                parent_id = item_to_parent.get(s["item_id"])
                if parent_id in grades:
                    grades[parent_id][s["grade"]] += s["count"]
                    total[parent_id] += s["count"] * s["grade"]
                    votes[parent_id] += s["count"]
                if parent_id != s["item_id"] and s["item_id"] in grades:
                    grades[s["item_id"]][s["grade"]] += s["count"]
                    total[s["item_id"]] += s["count"] * s["grade"]
                    votes[s["item_id"]] += s["count"]
        # Format results
        result = {}
        for item_id in item_ids:
            count = votes[item_id]
            if count < MIN_RATING_COUNT:
                result[item_id] = {
                    "average": None,
                    "count": count,
                    "distribution": [0] * 5,
                }
            else:
                result[item_id] = {
                    "average": round(total[item_id] / count, 1),
                    "count": count,
                    "distribution": [
                        100 * (grades[item_id][1] + grades[item_id][2]) // count,
                        100 * (grades[item_id][3] + grades[item_id][4]) // count,
                        100 * (grades[item_id][5] + grades[item_id][6]) // count,
                        100 * (grades[item_id][7] + grades[item_id][8]) // count,
                        100 * (grades[item_id][9] + grades[item_id][10]) // count,
                    ],
                }

        return result

    @staticmethod
    def get_rating_for_item(item: Item) -> float | None:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.__class__ in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(average=Avg("grade"), count=Count("item"))
        return round(stat["average"], 1) if stat["count"] >= MIN_RATING_COUNT else None

    @staticmethod
    def get_rating_count_for_item(item: Item) -> int:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.__class__ in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.pk])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(count=Count("item"))
        return stat["count"]

    @staticmethod
    def get_rating_distribution_for_item(item: Item):
        stat = Rating.objects.filter(grade__isnull=False)
        if item.__class__ in RATING_INCLUDES_CHILD_ITEMS:
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

    @classmethod
    def attach_to_items(cls, items: list[Item]) -> list[Item]:
        ratings = Rating.get_info_for_items(items)
        for i in items:
            i.rating_info = ratings.get(i.pk, {})
        return items

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
