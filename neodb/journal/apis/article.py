from datetime import datetime
from typing import Any, List

from django import forms
from django.conf import settings
from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import Field, File, Schema, Status
from ninja.files import UploadedFile
from ninja.pagination import paginate

from common.api import (
    NOT_FOUND,
    OK,
    OptionalOAuthAccessTokenAuth,
    PageNumberPagination,
    Result,
    api,
)
from common.sentry import record_activity

from ..models import Article
from ..models.collection import COVER_MAX_BYTES
from ..models.common import prefetch_latest_posts


class ArticleSchema(Schema):
    uuid: str
    url: str
    api_url: str
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
    created_time: datetime
    title: str
    body: str
    summary: str
    sensitive: bool = False
    language: str = ""
    tags: list[str] = []
    html_content: str
    cover_image_url: str | None = None


class ArticleInSchema(Schema):
    # title/language are bounded to the model's CharField limits so over-long
    # input returns a clean 422 instead of an uncaught DB DataError (500).
    # body/summary are unbounded TextFields, so no cap here.
    title: str = Field(max_length=500)
    body: str
    summary: str = ""
    sensitive: bool = False
    visibility: int = Field(ge=0, le=2)
    language: str = Field("", max_length=8)
    tags: list[str] = []
    post_to_fediverse: bool = False


class ArticlePageNumberPagination(PageNumberPagination):
    """Pagination that batch-prefetches latest posts after slicing.

    Plain ``PageNumberPagination`` serializes each Article one at a time, and
    ``ArticleSchema.post_id`` (``latest_post_id``) would then trigger its own
    ``PiecePost`` query per row (N+1). This hook resolves the whole page's
    latest posts in one batched query.
    """

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        if isinstance(val, tuple):
            return val
        data = val.get("data") if isinstance(val, dict) else None
        if data:
            prefetch_latest_posts(list(data))
        return val


@api.get(
    "/me/article/",
    response={200: List[ArticleSchema], 401: Result, 403: Result},
    tags=["article"],
)
@paginate(ArticlePageNumberPagination)
def list_articles(request):
    """
    Get articles by current user
    """
    return Article.objects.filter(owner=request.user.identity).order_by("-created_time")


@api.get(
    "/me/article/{article_uuid}",
    response={200: ArticleSchema, 401: Result, 403: Result, 404: Result},
    tags=["article"],
)
def get_user_article(request, article_uuid: str):
    """
    Get an article owned by current user by its uuid
    """
    # Owner-scoped lookup (returns 404, not 403, for someone else's article) so
    # the endpoint cannot be used to probe the existence of other users'
    # private articles, and to match update_article/delete_article.
    a = Article.get_by_url_and_owner(article_uuid, request.user.identity.pk)
    if not a:
        return NOT_FOUND
    return a


@api.post(
    "/me/article/",
    response={200: ArticleSchema, 401: Result, 403: Result},
    tags=["article"],
)
def create_article(request, a_in: ArticleInSchema):
    """
    Create an article for current user.

    `title` and `body` (markdown formatted) are required; `visibility` is
    required (0: public, 1: followers only, 2: private). `language` defaults
    to the user's preferred language when omitted.
    """
    article = Article.update_local_article(
        owner=request.user.identity,
        title=a_in.title,
        body=a_in.body,
        summary=a_in.summary,
        sensitive=a_in.sensitive,
        visibility=a_in.visibility,
        language=a_in.language or request.user.language,
        tags=a_in.tags,
        share_to_mastodon=a_in.post_to_fediverse,
        application_id=getattr(request, "application_id", None),
    )
    record_activity("article", "api")
    return article


@api.put(
    "/me/article/{article_uuid}",
    response={200: ArticleSchema, 401: Result, 403: Result, 404: Result},
    tags=["article"],
)
def update_article(request, article_uuid: str, a_in: ArticleInSchema):
    """
    Update an article owned by current user.
    """
    article = Article.get_by_url_and_owner(article_uuid, request.user.identity.pk)
    if not article:
        return NOT_FOUND
    article = Article.update_local_article(
        owner=request.user.identity,
        title=a_in.title,
        body=a_in.body,
        summary=a_in.summary,
        sensitive=a_in.sensitive,
        visibility=a_in.visibility,
        language=a_in.language or request.user.language,
        tags=a_in.tags,
        article=article,
        share_to_mastodon=a_in.post_to_fediverse,
        application_id=getattr(request, "application_id", None),
    )
    record_activity("article", "api")
    return article


@api.put(
    "/me/article/{article_uuid}/cover",
    response={200: ArticleSchema, 400: Result, 401: Result, 403: Result, 404: Result},
    tags=["article"],
)
def set_article_cover(request, article_uuid: str, cover: File[UploadedFile]):
    """
    Set the featured image of an article owned by current user.

    Send the image as `multipart/form-data` with a `cover` file field;
    it must be a valid image no larger than 5MB. Saving re-syncs the article's
    federated post and Bluesky records so the new image propagates.
    """
    article = Article.get_by_url_and_owner(article_uuid, request.user.identity.pk)
    if not article:
        return NOT_FOUND
    if (cover.size or 0) > COVER_MAX_BYTES:
        return Status(400, {"message": "Image file too large"})
    try:
        f = forms.ImageField().to_python(cover)
    except forms.ValidationError:
        return Status(400, {"message": "Invalid image file"})
    if f is None:
        return Status(400, {"message": "Invalid image file"})
    # normalize the filename from the detected format so the stored path gets
    # a meaningful extension even for extension-less uploads
    image = getattr(f, "image", None)  # set by ImageField.to_python
    fmt = (image.format or "").lower() if image else ""
    ext = {"jpeg": "jpg"}.get(fmt, fmt)
    if ext:
        f.name = f"cover.{ext}"
    article.cover = f
    article.application_id_when_save = getattr(request, "application_id", None)
    article.save()
    record_activity("article", "api")
    return article


@api.delete(
    "/me/article/{article_uuid}/cover",
    response={200: ArticleSchema, 401: Result, 403: Result, 404: Result},
    tags=["article"],
)
def remove_article_cover(request, article_uuid: str):
    """
    Remove the featured image of an article owned by current user.
    """
    article = Article.get_by_url_and_owner(article_uuid, request.user.identity.pk)
    if not article:
        return NOT_FOUND
    article.cover = settings.DEFAULT_ITEM_COVER
    article.application_id_when_save = getattr(request, "application_id", None)
    article.save()
    record_activity("article", "api")
    return article


@api.delete(
    "/me/article/{article_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["article"],
)
def delete_article(request, article_uuid: str):
    """
    Delete an article owned by current user.
    """
    article = Article.get_by_url_and_owner(article_uuid, request.user.identity.pk)
    if not article:
        return NOT_FOUND
    article.delete()
    return OK


@api.get(
    "/article/{article_uuid}",
    response={200: ArticleSchema, 401: Result, 403: Result, 404: Result},
    tags=["article"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def get_article(request, article_uuid: str):
    """
    Get an article by its uuid with permission checks.

    Returns the article if it is visible to the requesting user based on its
    visibility and the relationship to the owner; otherwise 403.
    """
    a = Article.get_by_url(article_uuid)
    if not a:
        return Status(404, {"message": "Article not found"})
    if not a.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    return a
