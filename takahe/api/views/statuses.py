from typing import Literal

from django.http import HttpRequest
from api.views import get_object_or_404
from django.utils import timezone
from pydantic import ConfigDict

from activities.models import (
    Post,
    PostAttachment,
    PostInteraction,
    PostInteractionStates,
    TimelineEvent,
)
from activities.models.post_types import (
    POLL_MAX_EXPIRATION,
    POLL_MAX_OPTION_CHARS,
    POLL_MAX_OPTIONS,
    POLL_MIN_EXPIRATION,
    poll_question,
)
from activities.models.scheduled_status import (
    API_VISIBILITIES,
    ScheduledStatus,
    ScheduledStatusError,
    check_backdate,
    parse_scheduled_at,
)
from activities.services import PostService
from core import sentry
from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from hatchway import ApiError, ApiResponse, Schema, api_view


class PostPollSchema(Schema):
    options: list[str]
    expires_in: int
    multiple: bool = False
    hide_totals: bool = False

    def validate_limits(self) -> None:
        if len(self.options) < 2:
            raise ApiError(
                422, "Validation failed: Options must have more than one item"
            )
        if any(not option.strip() for option in self.options):
            raise ApiError(422, "Validation failed: Options can't be blank")
        if len(self.options) > POLL_MAX_OPTIONS:
            raise ApiError(
                422,
                f"Validation failed: Options can't contain more than {POLL_MAX_OPTIONS} items",
            )
        if any(len(option) > POLL_MAX_OPTION_CHARS for option in self.options):
            raise ApiError(
                422,
                f"Validation failed: Options can't be longer than {POLL_MAX_OPTION_CHARS} characters each",
            )
        if len(set(self.options)) != len(self.options):
            raise ApiError(422, "Validation failed: Options contain duplicate items")
        if self.expires_in < POLL_MIN_EXPIRATION:
            raise ApiError(422, "Validation failed: Expiration is too soon")
        if self.expires_in > POLL_MAX_EXPIRATION:
            raise ApiError(
                422, "Validation failed: Expiration is too far into the future"
            )

    def dict(self):
        return poll_question(
            self.options, self.expires_in, self.multiple, self.hide_totals
        )


class PostStatusSchema(Schema):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    status: str | None = None
    in_reply_to_id: str | None = None
    quoted_status_id: str | None = None
    quote_id: str | None = None
    sensitive: bool = False
    spoiler_text: str | None = None
    visibility: Literal["public", "unlisted", "private", "direct"] = "public"
    language: str | None = None
    scheduled_at: str | None = None
    media_ids: list[str] = []
    poll: PostPollSchema | None = None


class MediaAttributesSchema(Schema):
    id: str
    description: str


class EditStatusSchema(Schema):
    status: str
    sensitive: bool = False
    spoiler_text: str | None = None
    language: str | None = None
    media_ids: list[str] = []
    media_attributes: list[MediaAttributesSchema] = []
    poll: PostPollSchema | None = None


def post_for_id(request: HttpRequest, id: str) -> Post:
    """
    Common logic to get a Post object for an ID, taking visibility into
    account.
    """
    if request.identity:
        queryset = Post.objects.not_hidden().visible_to(
            request.identity, include_replies=True, include_muted=True
        )
    else:
        queryset = Post.objects.not_hidden().unlisted()
    return get_object_or_404(queryset, pk=id)


def _resolve_owned_attachments(
    request: HttpRequest, media_ids: list[str]
) -> list[PostAttachment]:
    """
    Resolve a list of attachment IDs to PostAttachment rows owned by the
    caller. Raises 404 for unknown IDs, 403 for any attachment whose
    author is not the requesting identity (including null-author rows),
    and 422 for media held by a pending scheduled status.
    """
    attachments: list[PostAttachment] = []
    reserved = (
        ScheduledStatus.reserved_media_ids(request.identity) if media_ids else set()
    )
    for media_id in media_ids:
        attachment = get_object_or_404(PostAttachment, pk=media_id)
        if attachment.author_id != request.identity.pk:
            raise ApiError(403, "Not the owner of this attachment")
        if attachment.pk in reserved:
            raise ApiError(422, "Validation failed: Media is already attached")
        attachments.append(attachment)
    return attachments


@scope_required("write:statuses")
@api_view.post
def post_status(
    request, details: PostStatusSchema
) -> schemas.Status | schemas.ScheduledStatus:
    # Check text length
    if details.status and len(details.status) > request.config.post_length:
        raise ApiError(400, "Status is too long")
    if not details.status and not details.media_ids:
        raise ApiError(400, "Status is empty")
    if details.poll:
        if details.media_ids:
            raise ApiError(
                422, "Validation failed: Poll can't be attached to a post with media"
            )
        details.poll.validate_limits()
    # Grab attachments
    attachments = _resolve_owned_attachments(request, details.media_ids)
    scheduled_at = None
    backdate = None
    if details.scheduled_at:
        try:
            scheduled_at = parse_scheduled_at(details.scheduled_at)
            # Mastodon posts a status scheduled in the past right away;
            # with backdating on, the past time becomes its publish time
            if scheduled_at <= timezone.now():
                if request.config.post_backdating:
                    backdate = scheduled_at
                scheduled_at = None
            else:
                ScheduledStatus.check_time(request.identity, scheduled_at)
        except ScheduledStatusError as e:
            raise ApiError(422, str(e))
    if scheduled_at:
        # Media is held unattached until the status posts
        if any(a.post_id for a in attachments):
            raise ApiError(422, "Validation failed: Media is already attached")
    reply_post = None
    if details.in_reply_to_id:
        reply_post = post_for_id(request, details.in_reply_to_id)
    # Quoting is explicit only: the client must pass quoted_status_id
    # (Mastodon 4.5+) or its quote_id alias. URLs in the text stay text.
    quote_post = None
    quote_id = details.quoted_status_id or details.quote_id
    if quote_id:
        quote_post = (
            Post.objects.not_hidden()
            .visible_to(request.identity, include_replies=True)
            .filter(pk=quote_id)
            .first()
        )
        if quote_post is None:
            raise ApiError(404, "Quoted status not found")
    status_text = details.status or ""
    if backdate:
        try:
            check_backdate(
                request.identity,
                backdate,
                status_text,
                reply_post,
                quote_post,
                details.poll,
            )
        except ScheduledStatusError as e:
            raise ApiError(422, str(e))
    visibility = API_VISIBILITIES[details.visibility]
    if scheduled_at:
        scheduled = ScheduledStatus.create(
            author=request.identity,
            scheduled_at=scheduled_at,
            text=status_text,
            visibility=visibility,
            spoiler_text=details.spoiler_text,
            sensitive=details.sensitive,
            language=details.language,
            media_ids=[str(a.pk) for a in attachments],
            poll=details.poll.model_dump() if details.poll else None,
            in_reply_to_id=str(reply_post.pk) if reply_post else None,
            quoted_status_id=str(quote_post.pk) if quote_post else None,
            application=request.token.application if request.token else None,
        )
        return schemas.ScheduledStatus.from_scheduled_status(scheduled)
    # Enforce: quoting post visibility must not exceed quoted post visibility
    if quote_post:
        visibility = Post.clamp_quote_visibility(visibility, quote_post.visibility)
    post = Post.create_local(
        author=request.identity,
        content=status_text,
        summary=details.spoiler_text,
        sensitive=details.sensitive,
        visibility=visibility,
        reply_to=reply_post,
        quote=quote_post,
        attachments=attachments,
        question=details.poll.dict() if details.poll else None,
        language=details.language,
        application=request.token.application if request.token else None,
        published=backdate,
    )
    # Add their own timeline event for immediate visibility
    if not backdate:
        TimelineEvent.add_post(request.identity, post)
    sentry.record_activity("post", "api")
    return schemas.Status.from_post(post, identity=request.identity)


@scope_required("read:statuses")
@api_view.get
def status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:statuses")
@api_view.put
def edit_status(request, id: str, details: EditStatusSchema) -> schemas.Status:
    post = post_for_id(request, id)
    if post.author != request.identity:
        raise ApiError(401, "Not the author of this status")
    if post.in_reply_to is None and (
        post.type == "Article"
        or (
            isinstance(post.type_data, dict)
            and post.type_data.get("object", {}).get("relatedWith")
        )
    ):
        # NeoDB-managed structured posts (Reviews via relatedWith, and
        # standalone Articles) cannot be edited via Mastodon API without
        # corrupting the source markdown / metadata; users must edit them
        # in NeoDB.
        raise ApiError(422, "This post must be edited in NeoDB")
    if details.poll:
        if details.media_ids:
            raise ApiError(
                422, "Validation failed: Poll can't be attached to a post with media"
            )
        details.poll.validate_limits()
    # Grab attachments
    attachments = _resolve_owned_attachments(request, details.media_ids)
    # Update all details, as the client must provide them all
    post.edit_local(
        content=details.status,
        summary=details.spoiler_text,
        sensitive=details.sensitive,
        attachments=attachments,
        attachment_attributes=details.media_attributes,
        language=details.language,
        question=details.poll.dict() if details.poll else None,
    )
    return schemas.Status.from_post(post, identity=request.identity)


@scope_required("write:statuses")
@api_view.delete
def delete_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    if post.author != request.identity:
        raise ApiError(401, "Not the author of this status")
    PostService(post).delete()
    return schemas.Status.from_post(post, identity=request.identity)


@scope_required("read:statuses")
@api_view.get
def status_source(request, id: str) -> schemas.StatusSource:
    post = post_for_id(request, id)
    return schemas.StatusSource.from_post(post)


@scope_required("read:statuses")
@api_view.get
def status_context(request, id: str) -> schemas.Context:
    post = post_for_id(request, id)
    service = PostService(post)
    # Clients load a direct conversation from its last status, so walk as far
    # as Mastodon does for logged-out viewers; each hop costs queries here.
    ancestors, descendants = service.context(request.identity, num_ancestors=40)
    interactions = PostInteraction.get_post_interactions(
        ancestors + descendants, request.identity
    )
    return schemas.Context(
        ancestors=[
            schemas.Status.from_post(
                p, interactions=interactions, identity=request.identity
            )
            for p in reversed(ancestors)
        ],
        descendants=[
            schemas.Status.from_post(
                p, interactions=interactions, identity=request.identity
            )
            for p in descendants
        ],
    )


@scope_required("read:statuses")
@api_view.get
def status_history(request, id: str) -> list:
    # not implemented yet
    return []


@scope_required("write:favourites")
@api_view.post
def favourite_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    service = PostService(post)
    service.like_as(request.identity)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:favourites")
@api_view.post
def unfavourite_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    service = PostService(post)
    service.unlike_as(request.identity)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@api_view.get
def favourited_by(
    request: HttpRequest,
    id: str,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> ApiResponse[list[schemas.Account]]:
    """
    View who favourited a given status.
    """
    post = post_for_id(request, id)

    paginator = MastodonPaginator()
    pager: PaginationResult[PostInteraction] = paginator.paginate(
        post.interactions.filter(
            type=PostInteraction.Types.like,
            state__in=PostInteractionStates.group_active(),
        ).select_related("identity"),
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )

    return PaginatingApiResponse(
        [
            schemas.Account.from_identity(interaction.identity)
            for interaction in pager.results
        ],
        request=request,
        include_params=[
            "limit",
            "id",
        ],
    )


@api_view.get
def reblogged_by(
    request: HttpRequest,
    id: str,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> ApiResponse[list[schemas.Account]]:
    """
    View who reblogged a given status.
    """
    post = post_for_id(request, id)

    paginator = MastodonPaginator()
    pager: PaginationResult[PostInteraction] = paginator.paginate(
        post.interactions.filter(
            type=PostInteraction.Types.boost,
            state__in=PostInteractionStates.group_active(),
        ).select_related("identity"),
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )

    return PaginatingApiResponse(
        [
            schemas.Account.from_identity(interaction.identity)
            for interaction in pager.results
        ],
        request=request,
        include_params=[
            "limit",
            "id",
        ],
    )


@scope_required("write:favourites")
@api_view.post
def reblog_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    # A boost is announced publicly to the booster's followers, so only
    # public posts can be boosted by others, and direct posts never.
    if post.visibility == Post.Visibilities.mentioned or (
        post.visibility not in (Post.Visibilities.public, Post.Visibilities.unlisted)
        and post.author_id != request.identity.pk
    ):
        raise ApiError(403, "This action is not allowed")
    service = PostService(post)
    service.boost_as(request.identity)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:favourites")
@api_view.post
def unreblog_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    service = PostService(post)
    service.unboost_as(request.identity)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:bookmarks")
@api_view.post
def bookmark_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    request.identity.bookmarks.get_or_create(post=post)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, bookmarks={post.pk}, identity=request.identity
    )


@scope_required("write:bookmarks")
@api_view.post
def unbookmark_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    request.identity.bookmarks.filter(post=post).delete()
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:mutes")
@api_view.post
def mute_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    status = schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )
    status.muted = True
    return status


@scope_required("write:mutes")
@api_view.post
def unmute_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, interactions=interactions, identity=request.identity
    )


@scope_required("write:accounts")
@api_view.post
def pin_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    try:
        PostService(post).pin_as(request.identity)
        interactions = PostInteraction.get_post_interactions([post], request.identity)
        return schemas.Status.from_post(
            post, identity=request.identity, interactions=interactions
        )
    except ValueError as e:
        raise ApiError(422, str(e))


@scope_required("write:accounts")
@api_view.post
def unpin_status(request, id: str) -> schemas.Status:
    post = post_for_id(request, id)
    PostService(post).unpin_as(request.identity)
    interactions = PostInteraction.get_post_interactions([post], request.identity)
    return schemas.Status.from_post(
        post, identity=request.identity, interactions=interactions
    )


@api_view.get
def status_quotes(
    request: HttpRequest,
    id: str,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> ApiResponse[list[schemas.Status]]:
    """
    View statuses that quote the given status.
    """
    post = post_for_id(request, id)
    paginator = MastodonPaginator()
    pager: PaginationResult[Post] = paginator.paginate(
        Post.objects.not_hidden()
        .visible_to(request.identity, include_replies=True)
        .filter(quote_url=post.object_uri)
        .select_related("author"),
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    interactions = PostInteraction.get_post_interactions(
        pager.results, request.identity
    )
    return PaginatingApiResponse(
        [
            schemas.Status.from_post(
                quote_post,
                interactions=interactions,
                identity=request.identity,
            )
            for quote_post in pager.results
        ],
        request=request,
        include_params=["limit", "id"],
    )
