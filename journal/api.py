from datetime import datetime
from typing import List

from django.http import HttpResponse
from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.common.models import *
from common.api import *

from .models import (
    Collection,
    Mark,
    Review,
    ShelfType,
    Tag,
    TagManager,
    q_item_in_category,
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
    TagManager.tag_item(item, request.user.identity, mark.tags, mark.visibility)
    m.update(
        mark.shelf_type,
        mark.comment_text,
        mark.rating_grade,
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
    Remove a holding mark about an item for current user.
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    m = Mark(request.user.identity, item)
    m.delete()
    # skip tag deletion for now to be consistent with web behavior
    # TagManager.tag_item(item, request.user, [], 0)
    return 200, {"message": "OK"}


# Review


class ReviewSchema(Schema):
    url: str
    visibility: int = Field(ge=0, le=2)
    item: ItemSchema
    created_time: datetime
    title: str
    body: str
    html_content: str


class ReviewInSchema(Schema):
    visibility: int = Field(ge=0, le=2)
    created_time: datetime | None = None
    title: str
    body: str
    post_to_fediverse: bool = False


@api.get(
    "/me/review/",
    response={200: List[ReviewSchema], 401: Result, 403: Result},
    tags=["review"],
)
@paginate(PageNumberPagination)
def list_reviews(request, category: AvailableItemCategory | None = None):
    """
    Get reviews by current user

    `category` is optional, reviews for all categories will be returned if not specified.
    """
    queryset = Review.objects.filter(owner=request.user.identity)
    if category:
        queryset = queryset.filter(q_item_in_category(category))
    return queryset.prefetch_related("item")


@api.get(
    "/me/review/item/{item_uuid}",
    response={200: ReviewSchema, 401: Result, 403: Result, 404: Result},
    tags=["review"],
)
def get_review_by_item(request, item_uuid: str):
    """
    Get review on current user's shelf by item uuid
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    review = Review.objects.filter(owner=request.user.identity, item=item).first()
    if not review:
        return 404, {"message": "Review not found"}
    return review


@api.post(
    "/me/review/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["review"],
)
def review_item(request, item_uuid: str, review: ReviewInSchema):
    """
    Create or update a review about an item for current user.

    `title`, `body` (markdown formatted) and`visibility` are required;
    `created_time` is optional, default to now.
    if the item is already reviewed, this will update the review.
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    if review.created_time and review.created_time >= timezone.now():
        review.created_time = None
    Review.update_item_review(
        item,
        request.user.identity,
        review.title,
        review.body,
        review.visibility,
        created_time=review.created_time,
        share_to_mastodon=review.post_to_fediverse,
    )
    return 200, {"message": "OK"}


@api.delete(
    "/me/review/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["review"],
)
def delete_review(request, item_uuid: str):
    """
    Remove a review about an item for current user.
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    Review.update_item_review(item, request.user.identity, None, None)
    return 200, {"message": "OK"}


# Collection


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
