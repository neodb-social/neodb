import datetime
from typing import Any

from django.core.cache import cache
from django.db.models import QuerySet, prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Field, Schema, Status
from ninja.pagination import paginate

from catalog.models import (
    AvailableItemCategory,
    Item,
    ItemCategory,
    ItemSchema,
)
from common.api import (
    PageNumberPagination,
    RedirectedResult,
    Result,
    api,
    resolve_item_for_read,
    resolve_item_for_write,
)
from common.sentry import record_activity
from common.utils import get_uuid_or_404
from journal.models.common import (
    max_visiblity_to_user,
    prefetch_latest_posts,
    q_owned_piece_visible_to_user,
)
from journal.models.rating import Rating
from journal.models.shelf import ShelfMember
from takahe.utils import Takahe
from users.apis import UserIdentitySchema
from users.models.apidentity import APIdentity

from ..models import (
    Mark,
    Note,
    ShelfType,
)

CALENDAR_CACHE_SECONDS = 6 * 60 * 60


def _prefetch_shelf_members(members: list[ShelfMember]):
    """Batch-fetch related data for shelf members to avoid N+1 queries."""
    if not members:
        return
    items = [m.item for m in members]
    # Batch-fetch to avoid N+1; external_resources skips metadata (EGGPLANT-1DX).
    prefetch_related_objects(
        items,
        Item.external_resources_prefetch(),
        Item.credits_prefetch(),
    )
    Item.prefetch_parent_items(items)
    Item.prefetch_edition_works(items)
    Rating.attach_to_items(items)
    # Public item tags are not returned on the shelf; only the owner's own tags
    # (MarkSchema.tags, fetched below). ItemSchema.tags serializes as null
    # without a per-item aggregation (NEODB-SOCIAL-7KW).
    # Batch-fetch latest_post_id for all members to avoid N+1 queries
    # when MarkSchema accesses latest_post_id
    prefetch_latest_posts(members)
    # select_related("owner") gives each row its own APIdentity instance, so
    # MarkSchema.owner would fire a takahe lookup per row without this.
    Takahe.prefetch_takahe_identities([m.owner for m in members])
    # Batch-fetch user's tags for MarkSchema.tags
    owner = members[0].owner
    item_ids = [m.item_id for m in members]
    tags_by_item = owner.tag_manager.get_items_tags(item_ids)
    for m in members:
        m._tags = tags_by_item.get(m.item_id, [])


class ShelfPageNumberPagination(PageNumberPagination):
    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        _prefetch_shelf_members(val["data"])
        return val


# Mark
class MarkSchema(Schema):
    shelf_type: ShelfType
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
    item: ItemSchema
    # No uuid/url/api_url/id here, unlike the other content schemas: a mark is
    # not individually addressable (ShelfMember keeps the `Piece` default
    # url_path, which resolves to nothing), it is addressed by its item.
    owner: UserIdentitySchema
    created_time: datetime.datetime
    comment_text: str | None
    rating_grade: int | None = Field(ge=1, le=10)
    tags: list[str]


class MarkInSchema(Schema):
    shelf_type: ShelfType
    visibility: int = Field(ge=0, le=2)
    comment_text: str = ""
    rating_grade: int = Field(0, ge=0, le=10)
    tags: list[str] = []
    created_time: datetime.datetime | None = None
    post_to_fediverse: bool = False


class MarkLogSchema(Schema):
    shelf_type: ShelfType | None
    # item: ItemSchema
    timestamp: datetime.datetime
    comment_text: str | None
    rating_grade: int | None = Field(ge=1, le=10)
    progress_type: Note.ProgressType | None
    progress_value: str | None


class CalendarDaySchema(Schema):
    items: list[str]


class ProgressSchema(Schema):
    type: Note.ProgressType | None
    value: str | None


class ProgressInSchema(Schema):
    type: Note.ProgressType | None = None
    value: str | None = None


@api.get(
    "/user/{handle}/calendar",
    response={200: dict[str, CalendarDaySchema], 401: Result, 403: Result, 404: Result},
    tags=["shelf"],
)
def get_user_calendar_data(request, handle: str):
    """
    Get calendar data for a specific user.

    Response is a dict keyed by YYYY-MM-DD with {"items": [category, ...]}.
    Possible categories: book, movie, tv, music, game, podcast, performance, other.
    Note: result of this api may be cached for a few hours.
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        return Status(404, {"message": "User not found"})

    viewer = getattr(request.user, "identity", None)
    if not viewer:
        return Status(401, {"message": "Login required"})
    if request.user != target.user:
        if target.restricted or target.is_rejecting(viewer):
            return Status(403, {"message": "Access denied"})
    max_visibility = max_visiblity_to_user(request.user, target)
    cache_key = f"user_calendar:{target.pk}:{max_visibility}"
    calendar_data = cache.get_or_set(
        cache_key,
        lambda: target.shelf_manager.get_calendar_data(max_visibility),
        timeout=CALENDAR_CACHE_SECONDS,
    )
    return calendar_data


@api.get(
    "/user/{handle}/shelf/{type}",
    response={200: list[MarkSchema], 401: Result, 403: Result, 404: Result},
    tags=["shelf"],
)
@paginate(ShelfPageNumberPagination)
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
        .select_related("owner")
        .prefetch_related("item")
    )
    return queryset


@api.get(
    "/me/shelf/{type}",
    response={200: list[MarkSchema], 401: Result, 403: Result},
    tags=["shelf"],
)
@paginate(ShelfPageNumberPagination)
def list_marks_on_shelf(
    request, type: ShelfType, category: AvailableItemCategory | None = None
):
    """
    Get holding marks on current user's shelf

    Shelf's `type` should be one of `wishlist` / `progress` / `complete` / `dropped`;
    `category` is optional, marks for all categories will be returned if not specified.
    """
    queryset = (
        request.user.shelf_manager.get_latest_members(type, category)
        .select_related("owner")
        .prefetch_related("item")
    )
    return queryset


@api.get(
    "/me/shelf/item/{item_uuid}",
    response={
        200: MarkSchema,
        302: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def get_mark_by_item(request, item_uuid: str, response: HttpResponse):
    """
    Get holding mark on current user's shelf by item uuid

    If the item was merged into another one, HTTP 302 is returned.
    """
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/me/shelf/item/{uuid}", response
    )
    if not item:
        return redirect
    shelfmember = request.user.shelf_manager.locate_item(item)
    if not shelfmember:
        return Status(404, {"message": "Mark not found"})
    return shelfmember


@api.get(
    "/me/shelf/item/{item_uuid}/progress",
    response={
        200: ProgressSchema,
        302: RedirectedResult,
        400: Result,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def get_item_progress(request, item_uuid: str, response: HttpResponse):
    """Get progress of a marked item.

    If the item was merged into another one, HTTP 302 is returned.
    """
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/me/shelf/item/{uuid}/progress", response
    )
    if not item:
        return redirect
    mark = Mark(request.user.identity, item)
    if not mark.shelfmember:
        return Status(404, {"message": "Mark not found"})
    return {
        "type": mark.progress_type,
        "value": mark.progress_value,
    }


@api.post(
    "/me/shelf/item/{item_uuid}/progress",
    response={
        200: ProgressSchema,
        307: RedirectedResult,
        400: Result,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def set_item_progress(
    request,
    item_uuid: str,
    progress: ProgressInSchema,
    response: HttpResponse,
):
    """Set or clear progress of a marked item.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/shelf/item/{uuid}/progress", response
    )
    if not item:
        return redirect
    mark = Mark(request.user.identity, item)
    try:
        mark.set_progress(progress.type, progress.value)
    except ValueError as error:
        return Status(400, {"message": str(error)})
    record_activity("progress", "api")
    return {
        "type": mark.progress_type,
        "value": mark.progress_value,
    }


@api.delete(
    "/me/shelf/item/{item_uuid}/progress",
    response={
        200: Result,
        307: RedirectedResult,
        400: Result,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def delete_item_progress(request, item_uuid: str, response: HttpResponse):
    """Clear progress of a marked item.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/shelf/item/{uuid}/progress", response
    )
    if not item:
        return redirect
    mark = Mark(request.user.identity, item)
    try:
        mark.set_progress(None, None)
    except ValueError as error:
        return Status(400, {"message": str(error)})
    record_activity("progress", "api")
    return Status(200, {"message": "OK"})


@api.get(
    "/me/shelf/items/{item_uuids}",
    response={200: list[MarkSchema], 401: Result, 403: Result, 404: Result},
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
    response={
        200: Result,
        307: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def mark_item(request, item_uuid: str, mark: MarkInSchema, response: HttpResponse):
    """
    Create or update a holding mark about an item for current user.

    `shelf_type` and `visibility` are required; `created_time` is optional, default to now.
    if the item is already marked, this will update the mark.

    updating mark without `rating_grade`, `comment_text` or `tags` field will clear them.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/shelf/item/{uuid}", response
    )
    if not item:
        return redirect
    if mark.created_time:
        if mark.created_time.tzinfo is None:
            mark.created_time = timezone.make_aware(
                mark.created_time, datetime.timezone.utc
            )
        if mark.created_time > timezone.now():
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
        application_id=getattr(request, "application_id", None),
    )
    record_activity("mark", "api")
    return Status(200, {"message": "OK"})


@api.delete(
    "/me/shelf/item/{item_uuid}",
    response={
        200: Result,
        307: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
def delete_mark(request, item_uuid: str, response: HttpResponse):
    """
    Remove a holding mark about an item for current user, unlike the web behavior, this does not clean up tags.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/shelf/item/{uuid}", response
    )
    if not item:
        return redirect
    m = Mark(request.user.identity, item)
    m.delete(keep_tags=True)
    return Status(200, {"message": "OK"})


@api.get(
    "/me/shelf/item/{item_uuid}/logs",
    response={
        200: list[MarkLogSchema],
        302: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["shelf"],
)
@paginate(PageNumberPagination)
def get_mark_logs_by_item(request, item_uuid: str, response: HttpResponse):
    """
    Get holding mark on current user's shelf by item uuid

    If the item was merged into another one, HTTP 302 is returned.
    """
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/me/shelf/item/{uuid}/logs", response
    )
    if not item:
        return redirect
    return Mark(request.user.identity, item).logs
