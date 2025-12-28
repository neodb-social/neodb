from typing import List

from django.core.cache import cache
from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.models import Item, ItemSchema
from common.api import PageNumberPagination, Result, api

from ..models import Tag


class TagSchema(Schema):
    uuid: str
    title: str
    visibility: int = Field(ge=0, le=2)


class TagInSchema(Schema):
    title: str
    visibility: int = Field(ge=0, le=2)


class TagItemSchema(Schema):
    item: ItemSchema


class TagItemInSchema(Schema):
    item_uuid: str


@api.get(
    "/me/tag/",
    response={200: List[TagSchema], 401: Result, 403: Result},
    tags=["tag"],
)
@paginate(PageNumberPagination)
def list_tags(request, title: str | None = None):
    """
    Get tags created by current user

    `title` is optional, all tags will be returned if not specified.
    """
    queryset = Tag.objects.filter(owner=request.user.identity)
    if title:
        queryset = queryset.filter(title=Tag.cleanup_title(title))
    return queryset


@api.get(
    "/me/tag/{tag_uuid}",
    response={200: TagSchema, 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
def get_tag(request, tag_uuid: str):
    """
    Get tags by its uuid
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    return tag


@api.post(
    "/me/tag/",
    response={200: TagSchema, 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
def create_tag(request, t_in: TagInSchema):
    """
    Create tag.

    `title` is required, `visibility` can only be 0 or 2; if tag with same title exists, existing tag will be returned.
    """
    title = Tag.cleanup_title(t_in.title)
    visibility = 2 if t_in.visibility else 0
    tag, created = Tag.objects.get_or_create(
        owner=request.user.identity,
        title=title,
        defaults={"visibility": visibility},
    )
    if not created:
        tag.visibility = visibility
        tag.save()
    return tag


@api.put(
    "/me/tag/{tag_uuid}",
    response={200: TagSchema, 401: Result, 403: Result, 404: Result, 409: Result},
    tags=["tag"],
)
def update_tag(request, tag_uuid: str, t_in: TagInSchema):
    """
    Update tag.

    rename tag with an existing title will return HTTP 409 error
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    title = Tag.cleanup_title(tag.title)
    visibility = 2 if t_in.visibility else 0
    if title != tag.title:
        try:
            tag.title = title
            tag.visibility = visibility
            tag.save()
        except Exception:
            return 409, {"message": "Tag with same title exists"}
    return tag


@api.delete(
    "/me/tag/{tag_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
def delete_tag(request, tag_uuid: str):
    """
    Remove a tag.
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    tag.delete()
    return 200, {"message": "OK"}


@api.get(
    "/me/tag/{tag_uuid}/item/",
    response={200: List[TagItemSchema], 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
@paginate(PageNumberPagination)
def tag_list_items(request, tag_uuid: str):
    """
    Get items in a tag tags
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    return tag.members.all()


@api.post(
    "/me/tag/{tag_uuid}/item/",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
def tag_add_item(request, tag_uuid: str, tag_item: TagItemInSchema):
    """
    Add an item to tag
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    if not tag_item.item_uuid:
        return 404, {"message": "Item not found"}
    item = Item.get_by_url(tag_item.item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    tag.append_item(item)
    return 200, {"message": "OK"}


@api.delete(
    "/me/tag/{tag_uuid}/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["tag"],
)
def tag_delete_item(request, tag_uuid: str, item_uuid: str):
    """
    Remove an item from tag
    """
    tag = Tag.get_by_url(tag_uuid)
    if not tag:
        return 404, {"message": "Tag not found"}
    if tag.owner != request.user.identity:
        return 403, {"message": "Not owner"}
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    tag.remove_item(item)
    return 200, {"message": "OK"}


@api.get(
    "/trending/tag/",
    response={200: list[str]},
    auth=None,
    tags=["trending"],
)
def trending_tag(request):
    """
    Get popular tags site-wide
    """
    return cache.get("popular_tags", [])
