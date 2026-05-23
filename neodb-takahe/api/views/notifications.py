import dataclasses

from activities.models import PostInteraction, TimelineEvent
from activities.services import TimelineService
from django.http import Http404, HttpRequest
from api.views import get_object_or_404
from hatchway import ApiResponse, QueryOrBody, api_view

from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult

# Types/exclude_types use weird syntax so we have to handle them manually
NOTIFICATION_TYPES = {
    "status": TimelineEvent.Types.post,
    "favourite": TimelineEvent.Types.liked,
    "reblog": TimelineEvent.Types.boosted,
    "mention": TimelineEvent.Types.mentioned,
    "follow": TimelineEvent.Types.followed,
    "follow_request": TimelineEvent.Types.follow_requested,
    "quote": TimelineEvent.Types.quoted,
    "admin.sign_up": TimelineEvent.Types.identity_created,
}


@scope_required("read:notifications")
@api_view.get
def notifications(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
    account_id: str | None = None,
) -> ApiResponse[list[schemas.Notification]]:
    requested_types = set(request.GET.getlist("types[]"))
    excluded_types = set(request.GET.getlist("exclude_types[]"))
    if not requested_types:
        requested_types = set(NOTIFICATION_TYPES.keys())
    requested_types.difference_update(excluded_types)
    # Use that to pull relevant events
    queryset = TimelineService(request.identity).notifications(
        [NOTIFICATION_TYPES[r] for r in requested_types if r in NOTIFICATION_TYPES]
    )
    paginator = MastodonPaginator()
    pager: PaginationResult[TimelineEvent] = paginator.paginate(
        queryset,
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    interactions = PostInteraction.get_event_interactions(
        pager.results,
        request.identity,
    )
    return PaginatingApiResponse(
        [
            schemas.Notification.from_timeline_event(event, interactions=interactions)
            for event in pager.results
        ],
        request=request,
        include_params=["limit", "account_id"],
    )


@scope_required("read:notifications")
@api_view.get
def get_notification(
    request: HttpRequest,
    id: str,
) -> schemas.Notification:
    if not id.isdigit():
        raise Http404
    notification = get_object_or_404(
        TimelineService(request.identity).notifications(
            list(NOTIFICATION_TYPES.values())
        ),
        id=id,
    )
    return schemas.Notification.from_timeline_event(notification)


@scope_required("read:notifications")
@api_view.get
def unread_count(request: HttpRequest) -> dict:
    requested_types = set(request.GET.getlist("types[]"))
    excluded_types = set(request.GET.getlist("exclude_types[]"))
    if not requested_types:
        requested_types = set(NOTIFICATION_TYPES.keys())
    requested_types.difference_update(excluded_types)
    queryset = TimelineService(request.identity).notifications(
        [NOTIFICATION_TYPES[r] for r in requested_types if r in NOTIFICATION_TYPES]
    )
    limit = min(int(request.GET.get("limit", 1000)), 1000)
    return {"count": min(queryset.count(), limit)}


@scope_required("write:notifications")
@api_view.post
def dismiss_notifications(request: HttpRequest) -> dict:
    TimelineService(request.identity).notifications(
        list(NOTIFICATION_TYPES.values())
    ).update(dismissed=True)

    return {}


@scope_required("write:notifications")
@api_view.post
def dismiss_notification(request: HttpRequest, id: str) -> dict:
    notification = get_object_or_404(
        TimelineService(request.identity).notifications(
            list(NOTIFICATION_TYPES.values())
        ),
        id=id,
    )

    notification.dismissed = True
    notification.save()

    return {}


# NOTE: Policy values are persisted per identity but not yet enforced — notifications
# are not actually filtered or dropped based on these settings. The summary counts
# also always return 0 for the same reason.
POLICY_VALUES = {"accept", "filter", "drop"}


@scope_required("read:notifications")
@api_view.get
def get_notifications_policy(request: HttpRequest) -> schemas.NotificationPolicy:
    return schemas.NotificationPolicy.from_identity(request.identity)


@scope_required("write:notifications")
@api_view.patch
def update_notifications_policy(
    request: HttpRequest,
    for_not_following: QueryOrBody[str | None] = None,
    for_not_followers: QueryOrBody[str | None] = None,
    for_new_accounts: QueryOrBody[str | None] = None,
    for_private_mentions: QueryOrBody[str | None] = None,
    for_limited_accounts: QueryOrBody[str | None] = None,
) -> schemas.NotificationPolicy:
    mapping = {
        "notification_policy_not_following": for_not_following,
        "notification_policy_not_followers": for_not_followers,
        "notification_policy_new_accounts": for_new_accounts,
        "notification_policy_private_mentions": for_private_mentions,
        "notification_policy_limited_accounts": for_limited_accounts,
    }
    update = {k: v for k, v in mapping.items() if v is not None and v in POLICY_VALUES}
    if update:
        request.identity.config_identity = dataclasses.replace(
            request.identity.config_identity, **update
        )
        request.identity.save()
    return schemas.NotificationPolicy.from_identity(request.identity)
