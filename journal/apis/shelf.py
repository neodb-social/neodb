from datetime import datetime
from typing import List

from django.http import HttpResponse
from django.utils import timezone
from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.common.models import AvailableItemCategory, Item, ItemCategory, ItemSchema
from common.api import PageNumberPagination, Result, api
from common.utils import get_uuid_or_404
from journal.models.common import q_owned_piece_visible_to_user
from journal.models.shelf import ShelfMember
from users.models.apidentity import APIdentity

from ..models import (
    Mark,
    ShelfType,
)


# Mark
class MarkSchema(Schema):
    shelf_type: ShelfType
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
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


class MarkLogSchema(Schema):
    shelf_type: ShelfType | None
    # item: ItemSchema
    timestamp: datetime


@api.get(
    "/user/{handle}/shelf/{type}",
    response={200: List[MarkSchema], 401: Result, 403: Result, 404: Result},
    tags=["shelf"],
)
@paginate(PageNumberPagination)
def list_marks_on_user_shelf(
    request,
    handle: str,
    type: ShelfType,
    category: AvailableItemCategory | None = None,
):
    """
    Get holding marks on a specific user's shelf

    Shelf's `type` should be one of `wishlist` / `progress` / `complete` / `dropped`;
    `category` is optional, marks for all categories will be returned if not specified.
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        return ShelfMember.objects.none()
    qv = q_owned_piece_visible_to_user(request.user, target, True)
    queryset = (
        target.shelf_manager.get_latest_members(
            type, ItemCategory(category) if category else None
        )
        .filter(qv)
        .prefetch_related("item")
    )
    return queryset


@api.get(
    "/me/shelf/{type}",
    response={200: List[MarkSchema], 401: Result, 403: Result},
    tags=["shelf"],
)
@paginate(PageNumberPagination)
def list_marks_on_shelf(
    request, type: ShelfType, category: AvailableItemCategory | None = None
):
    """
    Get holding marks on current user's shelf

    Shelf's `type` should be one of `wishlist` / `progress` / `complete` / `dropped`;
    `category` is optional, marks for all categories will be returned if not specified.
    """
    queryset = request.user.shelf_manager.get_latest_members(
        type, category
    ).prefetch_related("item")
    return queryset


@api.get(
    "/me/shelf/item/{item_uuid}",
    response={200: MarkSchema, 302: Result, 401: Result, 403: Result, 404: Result},
    tags=["shelf"],
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


@api.get(
    "/me/shelf/items/{item_uuids}",
    response={200: List[MarkSchema], 401: Result},
    tags=["shelf"],
)
def get_marks_by_item_list(request, item_uuids: str, response: HttpResponse):
    """
    Get a list of holding mark on current user's shelf by a list of item uuids.

    Input should be no more than 20, comma-separated.
    Output has no guarenteed order, and may has less items than input,
    as some items may be merged/deleted or not marked.
    """
    uuids = [get_uuid_or_404(uid) for uid in item_uuids.split(",")[:20]]
    items = Item.objects.filter(
        uid__in=uuids, is_deleted=False, merged_to_item__isnull=True
    )
    marks = Mark.get_marks_by_items(request.user.identity, items, request.user)
    return [m for m in marks.values() if m.shelf_type]


@api.post(
    "/me/shelf/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["shelf"],
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
        mark.created_time = timezone.now()
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
    tags=["shelf"],
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


@api.get(
    "/me/shelf/item/{item_uuid}/logs",
    response={
        200: List[MarkLogSchema],
        302: Result,
        401: Result,
        404: Result,
    },
    tags=["shelf"],
)
@paginate(PageNumberPagination)
def get_mark_logs_by_item(request, item_uuid: str, response: HttpResponse):
    """
    Get holding mark on current user's shelf by item uuid
    """
    item = Item.get_by_url(item_uuid)
    if not item or item.is_deleted:
        return 404, {"message": "Item not found"}
    if item.merged_to_item:
        response["Location"] = f"/api/me/shelf/item/{item.merged_to_item.uuid}/logs"
        return 302, {"message": "Item merged", "url": item.merged_to_item.api_url}
    return Mark(request.user.identity, item).logs
