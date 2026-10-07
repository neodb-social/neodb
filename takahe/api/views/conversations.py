import urllib.parse

from django.http import HttpRequest
from api.views import get_object_or_404

from activities.models.conversation import Conversation, ConversationMembership
from activities.services import TimelineService
from api import schemas
from api.decorators import scope_required
from hatchway import ApiResponse, api_view


def _link_header(
    request: HttpRequest, conversations: list[Conversation], limit: int | None
) -> str | None:
    """
    Mastodon pages conversations by the id of their last status, so the
    cursors in the Link header are last status ids, not conversation ids.
    """
    if not conversations:
        return None
    base = request.build_absolute_uri(request.path)
    extra = {"limit": str(limit)} if limit else {}

    def part(param: str, value: int | None, rel: str) -> str:
        query = urllib.parse.urlencode({**extra, param: value})
        return f'<{base}?{query}>; rel="{rel}"'

    return ", ".join(
        [
            part("max_id", conversations[-1].last_post_id, "next"),
            part("min_id", conversations[0].last_post_id, "prev"),
        ]
    )


@scope_required("read:statuses")
@api_view.get
def list_conversations(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> ApiResponse[list[schemas.Conversation]]:
    limit = max(1, min(limit, 40))
    queryset = TimelineService(request.identity).conversations()
    reverse = False
    try:
        if max_id:
            queryset = queryset.filter(last_post_id__lt=int(max_id))
        if since_id:
            queryset = queryset.filter(last_post_id__gt=int(since_id))
        if min_id:
            # Items immediately newer than min_id: take them oldest first
            queryset = queryset.filter(last_post_id__gt=int(min_id))
            reverse = True
    except ValueError:
        return ApiResponse([])
    if reverse:
        queryset = queryset.order_by("last_post_id")
    conversations = list(queryset[:limit])
    if reverse:
        conversations.reverse()
    response = ApiResponse(
        [
            schemas.Conversation.from_conversation(conv, request.identity)
            for conv in conversations
        ]
    )
    link = _link_header(request, conversations, int(request.GET.get("limit") or 0))
    if link:
        response.headers["link"] = link
    return response


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


def _set_unread(request: HttpRequest, id: str, unread: bool) -> schemas.Conversation:
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
    membership.unread = unread
    membership.save(update_fields=["unread", "updated"])
    return schemas.Conversation.from_conversation(conversation, request.identity)


@scope_required("write:conversations")
@api_view.post
def mark_conversation_read(request: HttpRequest, id: str) -> schemas.Conversation:
    return _set_unread(request, id, False)


@scope_required("write:conversations")
@api_view.post
def mark_conversation_unread(request: HttpRequest, id: str) -> schemas.Conversation:
    return _set_unread(request, id, True)
