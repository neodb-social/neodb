from datetime import datetime

from django.http import HttpRequest
from ninja import Schema, Status
from ninja.pagination import paginate

from catalog.core import (
    CatalogRef,
    CoreClientError,
    CoreDegradedError,
    CoreNotFoundError,
)
from catalog.services import ensure_release_item
from common.api import PageNumberPagination, Result, api

from ..models import CollectionItem


class CabinetAddSchema(Schema):
    core_catalog_ref: str


class CabinetItemSchema(Schema):
    collection_item_uid: str
    created_at: datetime
    item_uid: str
    core_catalog_ref: str
    title: str
    artist: list[str]
    company: list[str]
    released: str | None
    media_format: list[str]
    genre: list[str]


def _cabinet_card(copy: CollectionItem) -> dict:
    album = copy.item
    return {
        "collection_item_uid": copy.uuid,
        "created_at": copy.created_at,
        "item_uid": album.uuid,
        "core_catalog_ref": album.core_catalog_ref,
        "title": album.title,
        "artist": list(album.artist or []),
        "company": list(album.company or []),
        "released": album.release_date,
        "media_format": album.display_media_formats,
        "genre": list(album.genre or []),
    }


def _parse_release_ref(value: str) -> CatalogRef | None:
    try:
        ref = CatalogRef.parse(value)
    except TypeError, ValueError:
        return None
    return ref if ref.entity_type == "release" else None


@api.post(
    "/me/cabinet/",
    response={201: CabinetItemSchema, 400: Result, 404: Result, 503: Result},
    tags=["cabinet"],
)
def add_cabinet_copy(request: HttpRequest, payload: CabinetAddSchema):
    ref = _parse_release_ref(payload.core_catalog_ref)
    if ref is None:
        return Status(400, {"message": "Invalid Core release reference"})
    try:
        album = ensure_release_item(ref)
    except CoreNotFoundError:
        return Status(404, {"message": "Release not found"})
    except CoreDegradedError:
        return Status(503, {"message": "Catalog temporarily unavailable"})
    except CoreClientError:
        return Status(503, {"message": "Catalog temporarily unavailable"})

    copy = CollectionItem.objects.create(owner=request.user, item=album)
    return Status(201, _cabinet_card(copy))


@api.get(
    "/me/cabinet/",
    response={200: list[CabinetItemSchema], 401: Result, 403: Result},
    tags=["cabinet"],
)
@paginate(PageNumberPagination)
def list_cabinet_copies(request: HttpRequest):
    return (
        CollectionItem.objects.filter(owner=request.user)
        .select_related("item")
        .order_by("-created_at", "-pk")
    )


@api.get(
    "/me/cabinet/{collection_item_uid}/",
    response={200: CabinetItemSchema, 401: Result, 403: Result, 404: Result},
    tags=["cabinet"],
)
def get_cabinet_copy(request: HttpRequest, collection_item_uid: str):
    target = CollectionItem.get_by_url(collection_item_uid)
    copy = (
        CollectionItem.objects.filter(
            uid=target.uid if target else None,
            owner=request.user,
        )
        .select_related("item")
        .first()
    )
    if copy is None:
        return Status(404, {"message": "Cabinet copy not found"})
    return _cabinet_card(copy)


@api.delete(
    "/me/cabinet/{collection_item_uid}/",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["cabinet"],
)
def remove_cabinet_copy(request: HttpRequest, collection_item_uid: str):
    target = CollectionItem.get_by_url(collection_item_uid)
    if target is None or target.owner.pk != request.user.pk:
        return Status(404, {"message": "Cabinet copy not found"})
    target.delete()
    return Status(200, {"message": "OK"})
