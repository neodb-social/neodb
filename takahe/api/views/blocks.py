from django.http import HttpRequest

from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from hatchway import api_view
from users.models.block import Block


@scope_required("read:blocks")
@api_view.get
def blocks(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 20,
) -> list[schemas.Account]:
    queryset = (
        request.identity.outbound_blocks.active()
        .filter(mute=False)
        .select_related("target")
    )
    paginator = MastodonPaginator()
    pager: PaginationResult[Block] = paginator.paginate(
        queryset,
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    return PaginatingApiResponse(
        [schemas.Account.from_identity(ident.target) for ident in pager.results],
        request=request,
        include_params=["limit"],
    )
