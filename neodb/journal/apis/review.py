from datetime import datetime
from typing import Any

from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Field, Schema, Status
from ninja.pagination import paginate

from catalog.models import AvailableItemCategory, ItemSchema
from common.api import (
    OptionalOAuthAccessTokenAuth,
    PageNumberPagination,
    RedirectedResult,
    Result,
    deprecated_field,
    renamed_field,
    api,
    resolve_item_for_read,
    resolve_item_for_write,
)
from common.sentry import record_activity
from takahe.utils import Takahe
from users.apis import UserIdentitySchema

from ..models import (
    Review,
    q_item_in_category,
)


class ReviewSchema(Schema):
    id: str = Field(alias="absolute_url")
    uuid: str
    url: str
    api_url: str
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
    item: ItemSchema
    owner: UserIdentitySchema
    created_time: datetime
    title: str
    # body is deprecated, use content instead (as in NoteSchema)
    body: str = Field(deprecated=True)
    content: str = Field(alias="body")
    html_content: str


class ReviewInSchema(Schema):
    visibility: int = Field(ge=0, le=2)
    created_time: datetime | None = None
    title: str
    content: str = renamed_field("content", "body")
    body: str | None = deprecated_field()
    post_to_fediverse: bool = False


class ReviewPageNumberPagination(PageNumberPagination):
    """Pagination that batch-loads takahe identities after slicing.

    ``select_related("owner")`` hands each row its own ``APIdentity``, so
    ``ReviewSchema.owner`` would resolve display_name/avatar with one
    cross-db lookup per row.
    """

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        data = val.get("data")
        if data:
            Takahe.prefetch_takahe_identities([r.owner for r in data])
        return val


@api.get(
    "/me/review/",
    response={200: list[ReviewSchema], 401: Result, 403: Result},
    tags=["review"],
)
@paginate(ReviewPageNumberPagination)
def list_reviews(request, category: AvailableItemCategory | None = None):
    """
    Get reviews by current user

    `category` is optional, reviews for all categories will be returned if not specified.
    """
    queryset = Review.objects.filter(owner=request.user.identity).select_related(
        "owner"
    )
    if category:
        queryset = queryset.filter(q_item_in_category(category))
    return queryset.prefetch_related("item")


@api.get(
    "/me/review/item/{item_uuid}",
    response={
        200: ReviewSchema,
        302: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["review"],
)
def get_review_by_item(request, item_uuid: str, response: HttpResponse):
    """
    Get review on current user's shelf by item uuid

    If the item was merged into another one, HTTP 302 is returned.
    """
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/me/review/item/{uuid}", response
    )
    if not item:
        return redirect
    review = Review.objects.filter(owner=request.user.identity, item=item).first()
    if not review:
        return Status(404, {"message": "Review not found"})
    return review


@api.post(
    "/me/review/item/{item_uuid}",
    response={
        200: Result,
        307: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["review"],
)
def review_item(
    request, item_uuid: str, review: ReviewInSchema, response: HttpResponse
):
    """
    Create or update a review about an item for current user.

    `title`, `content` (markdown formatted) and `visibility` are required;
    `created_time` is optional, default to now.
    if the item is already reviewed, this will update the review.

    `body` is accepted as a deprecated alias of `content`.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/review/item/{uuid}", response
    )
    if not item:
        return redirect
    if review.created_time and review.created_time >= timezone.now():
        review.created_time = None
    Review.update_item_review(
        item,
        request.user.identity,
        review.title,
        review.content,
        review.visibility,
        created_time=review.created_time,
        share_to_mastodon=review.post_to_fediverse,
        application_id=getattr(request, "application_id", None),
    )
    record_activity("review", "api")
    return Status(200, {"message": "OK"})


@api.delete(
    "/me/review/item/{item_uuid}",
    response={
        200: Result,
        307: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["review"],
)
def delete_review(request, item_uuid: str, response: HttpResponse):
    """
    Remove a review about an item for current user.

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/review/item/{uuid}", response
    )
    if not item:
        return redirect
    Review.update_item_review(item, request.user.identity, None, None)
    return Status(200, {"message": "OK"})


@api.get(
    "/review/{review_uuid}",
    response={200: ReviewSchema, 401: Result, 403: Result, 404: Result},
    tags=["review"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def get_any_review(request, review_uuid: str):
    """
    Get a review by its uuid with permission checks.

    Returns the review if it is visible to the requesting user based on
    its visibility and the relationship to the owner; otherwise 403.
    """
    r = Review.get_by_url(review_uuid)
    if not r:
        return Status(404, {"message": "Review not found"})
    if not r.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    return r
