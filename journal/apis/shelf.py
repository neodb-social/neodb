from datetime import datetime
from typing import List

from django.http import HttpResponse
from django.utils import timezone
from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.common.models import AvailableItemCategory, Item, ItemSchema
from common.api import PageNumberPagination, Result, api

from ..models import (
    Mark,
    ShelfType,
)


# Mark
class MarkSchema(Schema):
    shelf_type: ShelfType
    visibility: int = Field(ge=0, le=2)

    item: ItemSchema
    created_time: datetime
    comment_text: str | None
    rating_grade: int | None = Field(ge=1, le=10)
    tags: list[str]


class MarkInSchema(Schema):
    shelf_type: ShelfType
    visibility: int = Field(ge=0, le=2)
    comment_text: str = ""
    rating_grade: int = Field(0, ge=0, le=10)
    tags: list[str] = []
    created_time: datetime | None = None
    post_to_fediverse: bool = False


@api.get(
    "/me/shelf/{type}",
    response={200: List[MarkSchema], 401: Result, 403: Result},
    tags=["mark"],
)
@paginate(PageNumberPagination)
def list_marks_on_shelf(
    request, type: ShelfType, category: AvailableItemCategory | None = None
):
    """
    Get holding marks on current user's shelf

    Shelf's `type` should be one of `wishlist` / `progress` / `complete`;
    `category` is optional, marks for all categories will be returned if not specified.
    """
    queryset = request.user.shelf_manager.get_latest_members(
        type, category
    ).prefetch_related("item")
    return queryset


@api.get(
    "/me/shelf/item/{item_uuid}",
    response={200: MarkSchema, 302: Result, 401: Result, 403: Result, 404: Result},
    tags=["mark"],
)
def get_mark_by_item(request, item_uuid: str, response: HttpResponse):
    """
    Get holding mark on current user's shelf by item uuid
    """
    item = Item.get_by_url(item_uuid)
    if not item or item.is_deleted:
        return 404, {"message": "Item not found"}
    if item.merged_to_item:
        response["Location"] = f"/api/me/shelf/item/{item.merged_to_item.uuid}"
        return 302, {"message": "Item merged", "url": item.merged_to_item.api_url}
    shelfmember = request.user.shelf_manager.locate_item(item)
    if not shelfmember:
        return 404, {"message": "Mark not found"}
    return shelfmember


@api.post(
    "/me/shelf/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["mark"],
)
def mark_item(request, item_uuid: str, mark: MarkInSchema):
    """
    Create or update a holding mark about an item for current user.

    `shelf_type` and `visibility` are required; `created_time` is optional, default to now.
    if the item is already marked, this will update the mark.

    updating mark without `rating_grade`, `comment_text` or `tags` field will clear them.
    """
    item = Item.get_by_url(item_uuid)
    if not item or item.is_deleted or item.merged_to_item:
        return 404, {"message": "Item not found"}
    if mark.created_time and mark.created_time >= timezone.now():
        mark.created_time = None
    m = Mark(request.user.identity, item)
    m.update(
        mark.shelf_type,
        mark.comment_text,
        mark.rating_grade,
        mark.tags,
        mark.visibility,
        created_time=mark.created_time,
        share_to_mastodon=mark.post_to_fediverse,
    )
    return 200, {"message": "OK"}


@api.delete(
    "/me/shelf/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["mark"],
)
def delete_mark(request, item_uuid: str):
    """
    Remove a holding mark about an item for current user, unlike the web behavior, this does not clean up tags.
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    m = Mark(request.user.identity, item)
    m.delete(keep_tags=True)
    return 200, {"message": "OK"}
