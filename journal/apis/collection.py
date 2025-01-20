from datetime import datetime
from typing import List

from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.common.models import Item, ItemSchema
from common.api import PageNumberPagination, Result, api

from ..models import Collection


class CollectionSchema(Schema):
    uuid: str
    url: str

    visibility: int = Field(ge=0, le=2)
    created_time: datetime
    title: str
    brief: str
    cover: str
    html_content: str


class CollectionInSchema(Schema):
    title: str
    brief: str
    visibility: int = Field(ge=0, le=2)


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
def list_collections(request):
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
def get_collection(request, collection_uuid: str):
    """
    Get collections by its uuid
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    return c


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
    c = Collection.objects.create(
        owner=request.user.identity,
        title=c_in.title,
        brief=c_in.brief,
        visibility=c_in.visibility,
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
    c.title = c_in.title
    c.brief = c_in.brief
    c.visibility = c_in.visibility
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
def collection_list_items(request, collection_uuid: str):
    """
    Get items in a collection collections
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return 404, {"message": "Collection not found"}
    if c.owner != request.user.identity:
        return 403, {"message": "Not owner"}
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
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    c.remove_item(item)
    return 200, {"message": "OK"}
