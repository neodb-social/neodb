from django.http import HttpRequest
from api.views import get_object_or_404

from activities.models.conversation import Conversation, ConversationMembership
from activities.services import TimelineService
from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from hatchway import ApiResponse, api_view


@scope_required("read:statuses")
@api_view.get
def list_conversations(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> ApiResponse[list[schemas.Conversation]]:
    if limit > 40:
        limit = 40
    queryset = TimelineService(request.identity).conversations()
    paginator = MastodonPaginator()
    pager: PaginationResult[Conversation] = paginator.paginate(
        queryset,
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    return PaginatingApiResponse(
        [
            schemas.Conversation.from_conversation(conv, request.identity)
            for conv in pager.results
        ],
        request=request,
        include_params=["limit"],
    )


@scope_required("write:conversations")
@api_view.delete
def delete_conversation(request: HttpRequest, id: str) -> dict:
    conversation = get_object_or_404(Conversation, pk=id)
    membership = get_object_or_404(
        ConversationMembership,
        conversation=conversation,
        identity=request.identity,
    )
    membership.dismissed = True
    membership.save(update_fields=["dismissed", "updated"])
    return {}


@scope_required("write:conversations")
@api_view.post
def mark_conversation_read(request: HttpRequest, id: str) -> schemas.Conversation:
    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "last_post",
            "last_post__author",
            "last_post__author__domain",
        ).prefetch_related(
            "participants",
            "participants__domain",
            "last_post__attachments",
            "last_post__mentions",
            "last_post__mentions__domain",
            "last_post__emojis",
        ),
        pk=id,
    )
    membership = get_object_or_404(
        ConversationMembership,
        conversation=conversation,
        identity=request.identity,
    )
    membership.unread = False
    membership.save(update_fields=["unread", "updated"])
    return schemas.Conversation.from_conversation(conversation, request.identity)
