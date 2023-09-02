from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import connection, models
from django.db.models import Avg, Count, Q
from django.utils.translation import gettext_lazy as _

from catalog.models import Item, ItemCategory
from users.models import User

from .common import Content

MIN_RATING_COUNT = 5
RATING_INCLUDES_CHILD_ITEMS = ["tvshow", "performance"]


class Rating(Content):
    class Meta:
        unique_together = [["owner", "item"]]

    grade = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(10), MinValueValidator(1)], null=True
    )

    @staticmethod
    def get_rating_for_item(item: Item) -> float | None:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.id])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(average=Avg("grade"), count=Count("item"))
        return round(stat["average"], 1) if stat["count"] >= MIN_RATING_COUNT else None

    @staticmethod
    def get_rating_count_for_item(item: Item) -> int:
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.id])
        else:
            stat = stat.filter(item=item)
        stat = stat.aggregate(count=Count("item"))
        return stat["count"]

    @staticmethod
    def get_rating_distribution_for_item(item: Item):
        stat = Rating.objects.filter(grade__isnull=False)
        if item.class_name in RATING_INCLUDES_CHILD_ITEMS:
            stat = stat.filter(item_id__in=item.child_item_ids + [item.id])
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
    def rate_item_by_user(
        item: Item, user: User, rating_grade: int | None, visibility: int = 0
    ):
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
    def get_item_rating_by_user(item: Item, user: User) -> int | None:
        rating = Rating.objects.filter(owner=user, item=item).first()
        return (rating.grade or None) if rating else None
