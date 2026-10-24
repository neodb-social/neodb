from django.http import HttpRequest
from hatchway import ApiError, QueryOrBody, api_view

from activities.models import ScheduledStatus, ScheduledStatusStates
from activities.models.scheduled_status import (
    ScheduledStatusError,
    parse_scheduled_at,
)
from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from api.views import get_object_or_404


def _pending(request: HttpRequest):
    return ScheduledStatus.objects.filter(
        author=request.identity, state=ScheduledStatusStates.scheduled
    )


@scope_required("read:statuses")
@api_view.get
def list_scheduled_statuses(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> list[schemas.ScheduledStatus]:
    paginator = MastodonPaginator()
    pager: PaginationResult[ScheduledStatus] = paginator.paginate(
        _pending(request),
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    return PaginatingApiResponse(
        [schemas.ScheduledStatus.from_scheduled_status(s) for s in pager.results],
        request=request,
        include_params=["limit"],
    )


@scope_required("read:statuses")
@api_view.get
def scheduled_status(request: HttpRequest, id: str) -> schemas.ScheduledStatus:
    scheduled = get_object_or_404(_pending(request), pk=id)
    return schemas.ScheduledStatus.from_scheduled_status(scheduled)


@scope_required("write:statuses")
@api_view.put
def update_scheduled_status(
    request: HttpRequest, id: str, scheduled_at: QueryOrBody[str | None] = None
) -> schemas.ScheduledStatus:
    scheduled = get_object_or_404(_pending(request), pk=id)
    if scheduled_at:
        try:
            when = parse_scheduled_at(scheduled_at)
            ScheduledStatus.check_time(request.identity, when, exclude_pk=scheduled.pk)
        except ScheduledStatusError as e:
            raise ApiError(422, str(e))
        if not scheduled.reschedule(when):
            raise ApiError(404, "Record not found")
    return schemas.ScheduledStatus.from_scheduled_status(scheduled)


@scope_required("write:statuses")
@api_view.delete
def delete_scheduled_status(request: HttpRequest, id: str) -> dict:
    scheduled = get_object_or_404(_pending(request), pk=id)
    scheduled.delete()
    return {}
