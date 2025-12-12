from datetime import datetime
from typing import List

from django.core.cache import cache
from django.utils import timezone
from django.views.decorators.cache import cache_page
from ninja import Field, Schema
from ninja.decorators import decorate_view
from ninja.pagination import paginate

from catalog.models import Item, ItemSchema
from common.api import PageNumberPagination, Result, api

from ..models import Collection


class CollectionSchema(Schema):
    uuid: str
    url: str
    api_url: str
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
    created_time: datetime
    title: str
    brief: str
    cover_image_url: str | None
    cover: str = Field(deprecated=True)
    html_content: str
    is_dynamic: bool
    query: str | None = None


class CollectionInSchema(Schema):
    title: str
    brief: str
    visibility: int = Field(ge=0, le=2)
    query: str | None = None


class CollectionItemSchema(Schema):
    item: ItemSchema
    note: str


class CollectionItemInSchema(Schema):
    item_uuid: str
    note: str


@api.get(
    "/me/collection/",
    response={200: List[CollectionSchema], 401: Result, 403: Result},
    tags=["collection"],
)
@paginate(PageNumberPagination)
def list_user_collections(request):
    """
    Get collections created by current user
    """
    queryset = Collection.objects.filter(owner=request.user.identity)
    return queryset


@api.get(
    "/me/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def get_user_collection(request, collection_uuid: str):
    """
    Get collections by its uuid
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    return c


@api.get(
    "/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
    auth=None,
)
def get_collection(request, collection_uuid: str):
    """
    Get details of a collection
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if not c.is_visible_to(request.user):
        return 403, {"message": "Permission denied"}
    return c


@api.get(
    "/collection/{collection_uuid}/item/",
    response={200: List[CollectionItemSchema], 401: Result, 403: Result, 404: Result},
    tags=["collection"],
    auth=None,
)
@paginate(PageNumberPagination)
def collection_list_items(request, collection_uuid: str):
    """
    Get items in a collection collections
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if not c.is_visible_to(request.user):
        return 403, {"message": "Permission denied"}
    if c.is_dynamic:
        items = c.query_result.items if c.query_result else []
        members = [{"item": i, "note": ""} for i in items]
        return members
    else:
        return c.ordered_members


@api.post(
    "/me/collection/",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def create_collection(request, c_in: CollectionInSchema):
    """
    Create collection.

    `title`, `brief` (markdown formatted) and `visibility` are required;
    """
    q = (c_in.query or "").strip() or None
    c = Collection.objects.create(
        owner=request.user.identity,
        title=c_in.title,
        brief=c_in.brief,
        visibility=c_in.visibility,
        query=q,
    )
    return c


@api.put(
    "/me/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def update_collection(request, collection_uuid: str, c_in: CollectionInSchema):
    """
    Update collection.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    q = (c_in.query or "").strip() or None
    is_dynamic = bool(q)
    if c.is_dynamic != is_dynamic:
        return 403, {"message": "Cannot change collection type"}
    c.title = c_in.title
    c.brief = c_in.brief
    c.visibility = c_in.visibility
    c.query = q
    c.save()
    return c


@api.delete(
    "/me/collection/{collection_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def delete_collection(request, collection_uuid: str):
    """
    Remove a collection.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    c.delete()
    return 200, {"message": "OK"}


@api.get(
    "/me/collection/{collection_uuid}/item/",
    response={200: List[CollectionItemSchema], 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
@paginate(PageNumberPagination)
def user_collection_list_items(request, collection_uuid: str):
    """
    Get items in a collection collections
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    if c.is_dynamic:
        items = c.query_result.items if c.query_result else []
        members = [{"item": i, "note": ""} for i in items]
        return members
    else:
        return c.ordered_members


@api.post(
    "/me/collection/{collection_uuid}/item/",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_add_item(
    request, collection_uuid: str, collection_item: CollectionItemInSchema
):
    """
    Add an item to collection
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    if c.is_dynamic:
        return 403, {"message": "Item list of dynamic collection cannot be updated"}
    if not collection_item.item_uuid:
        return 404, {"message": "Item not found"}
    item = Item.get_by_url(collection_item.item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    c.append_item(item, note=collection_item.note)
    return 200, {"message": "OK"}


@api.delete(
    "/me/collection/{collection_uuid}/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_delete_item(request, collection_uuid: str, item_uuid: str):
    """
    Remove an item from collection
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    if c.is_dynamic:
        return 403, {"message": "Item list of dynamic collection cannot be updated"}
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    c.remove_item(item)
    return 200, {"message": "OK"}


@api.get(
    "/trending/collection/",
    response={200: list[CollectionSchema]},
    summary="Trending collections",
    auth=None,
    tags=["trending"],
)
@decorate_view(cache_page(600))
def trending_collection(request):
    rot = timezone.now().minute // 6
    collection_ids = cache.get("featured_collections", [])
    i = rot * len(collection_ids) // 10
    collection_ids = collection_ids[i:] + collection_ids[:i]
    featured_collections = Collection.objects.filter(pk__in=collection_ids)
    return featured_collections
