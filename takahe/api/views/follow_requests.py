from django.http import HttpRequest
from hatchway import api_view

from api import schemas
from api.decorators import scope_required
from api.views.accounts import identity_for_client, relationship_for_client
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from users.models.identity import Identity
from users.services.identity import IdentityService


@scope_required("read:follows")
@api_view.get
def follow_requests(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 40,
) -> list[schemas.Account]:
    service = IdentityService(request.identity)
    paginator = MastodonPaginator(max_limit=80)
    pager: PaginationResult[Identity] = paginator.paginate(
        service.follow_requests(),
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    return PaginatingApiResponse(
        [schemas.Account.from_identity(i) for i in pager.results],
        request=request,
        include_params=["limit"],
    )


@scope_required("write:follows")
@api_view.post
def accept_follow_request(
    request: HttpRequest,
    id: str | None = None,
) -> schemas.Relationship:
    source_identity = identity_for_client(id)
    IdentityService(request.identity).accept_follow_request(source_identity)
    return relationship_for_client(source_identity, request.identity, id)


@scope_required("write:follows")
@api_view.post
def reject_follow_request(
    request: HttpRequest,
    id: str | None = None,
) -> schemas.Relationship:
    source_identity = identity_for_client(id)
    IdentityService(request.identity).reject_follow_request(source_identity)
    return relationship_for_client(source_identity, request.identity, id)
