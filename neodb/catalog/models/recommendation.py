from typing import TYPE_CHECKING

from django.db import models

from .item import Item


class ItemSimilarity(models.Model):
    """Top-K similar items per source item.

    Populated by the weekly batch job. Derived data: rows are recomputed in
    full per source on each run, so cascading deletes are intentional.
    """

    METHOD_SHELF_COOC = 0
    METHOD_TAG_COOC = 1
    METHOD_BLENDED = 2

    if TYPE_CHECKING:
        source_id: int
        target_id: int

    source = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="similarity_out"
    )
    target = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="similarity_in"
    )
    score = models.FloatField()
    method = models.PositiveSmallIntegerField(default=METHOD_SHELF_COOC)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_item_similarity"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target", "method"],
                name="catalog_item_similarity_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")),
                name="catalog_item_similarity_no_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "-score"],
                name="catalog_item_sim_src_score",
            ),
        ]


class UserRecommendation(models.Model):
    """Per-user precomputed recommendation list, refreshed nightly.

    Score is the raw producer output; visibility filtering happens at serve time.
    `seed_item_ids` lets the UI render "because you liked X". `category` is
    denormalized off ``Item`` so category-scoped queries avoid a join.
    """

    if TYPE_CHECKING:
        user_id: int
        item_id: int

    user = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="recommendations"
    )
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="recommended_to"
    )
    score = models.FloatField()
    seed_item_ids = models.JSONField(default=list)
    category = models.CharField(max_length=20, db_index=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_user_recommendation"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="catalog_user_reco_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "category", "-score"],
                name="catalog_user_reco_lookup",
            ),
        ]
