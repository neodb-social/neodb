"""Narrow Product services for Core-backed catalog items."""

from __future__ import annotations

import re
from collections.abc import Iterable

from django.db import IntegrityError, transaction

from catalog.core import CatalogRef, CatalogReleaseDetail, CoreClient
from catalog.models.music import Album
from common.models import MEDIA_FORMAT_CODES, normalize_genres, normalize_media_formats

_PARTIAL_RELEASE_DATE = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")


def _unique_strings(values: Iterable[str | None]) -> list[str]:
    return list(
        dict.fromkeys(value.strip() for value in values if value and value.strip())
    )


def _release_date(value: str | None) -> str | None:
    if value is None or not _PARTIAL_RELEASE_DATE.fullmatch(value):
        return None
    return value


def _snapshot_from_release(detail: CatalogReleaseDetail) -> dict:
    artists = _unique_strings(credit.display_name for credit in detail.artists)
    labels = _unique_strings(
        occurrence.display_name
        for occurrence in detail.provider_occurrences
        if (occurrence.entity_type or "").lower() == "label"
    )

    media_formats: list[str] = []
    for catalog_format in detail.formats:
        for value in (
            catalog_format.name,
            catalog_format.text,
            *catalog_format.descriptions,
        ):
            for normalized in normalize_media_formats(value):
                if normalized in MEDIA_FORMAT_CODES:
                    media_formats.append(normalized)

    return {
        "title": detail.title,
        "artist": artists,
        "company": labels,
        "release_date": _release_date(detail.released),
        "genre": normalize_genres([*detail.genres, *detail.styles]),
        "media_format": _unique_strings(media_formats),
        "album_type": [],
        "core_catalog_ref": str(detail.ref),
    }


def ensure_release_item(
    release_ref: CatalogRef, *, core_client: CoreClient | None = None
) -> Album:
    """Return or materialize exactly one Album for a Core Release reference."""

    if not isinstance(release_ref, CatalogRef):
        raise TypeError("release_ref must be a CatalogRef")
    if release_ref.entity_type != "release":
        raise ValueError("Release materialization requires a release CatalogRef")

    ref_value = str(release_ref)
    existing = Album.objects.filter(core_catalog_ref=ref_value).first()
    if existing is not None:
        return existing

    if core_client is None:
        with CoreClient.from_settings() as client:
            detail = client.get_release(release_ref)
    else:
        detail = core_client.get_release(release_ref)

    snapshot = _snapshot_from_release(detail)
    try:
        with transaction.atomic():
            album = Album.objects.create(**snapshot)
            # Keep the existing ItemCredit projection in step with the Album
            # credit fields used by Product serializers and search.
            album.sync_credits_from_metadata()
            return album
    except IntegrityError:
        # The unique Core anchor is the convergence point for a concurrent
        # first materialization. The atomic block rolls back the polymorphic
        # Item parent if this transaction lost the race.
        existing = Album.objects.filter(core_catalog_ref=ref_value).first()
        if existing is not None:
            return existing
        raise
