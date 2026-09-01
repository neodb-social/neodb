from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.signing import b62_decode, b62_encode
from django.db import models
from django.utils import timezone

from catalog.models import Album


class CollectionItem(models.Model):
    """One concrete physical copy owned by one Product user."""

    uid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cabinet_items",
    )
    item = models.ForeignKey(
        Album,
        on_delete=models.PROTECT,
        related_name="cabinet_copies",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = (
            models.Index(
                fields=["owner", "-created_at"], name="cabinet_owner_created_idx"
            ),
        )

    @property
    def uuid(self) -> str:
        return b62_encode(self.uid.int).zfill(22)

    @classmethod
    def get_by_url(cls, value: str) -> CollectionItem | None:
        try:
            item_uid = uuid.UUID(int=b62_decode(value))
        except TypeError, ValueError, OverflowError:
            return None
        return cls.objects.filter(uid=item_uid).first()

    def save(self, *args, **kwargs):
        item_id = getattr(self, "item_id", None)
        if (
            item_id
            and not Album.objects.filter(
                pk=item_id, core_catalog_ref__isnull=False
            ).exists()
        ):
            raise ValidationError("Cabinet copies require a Core release-backed Album")
        return super().save(*args, **kwargs)
