from api.views import get_object_or_404

from activities.models import Post
from api import schemas
from api.decorators import scope_required
from hatchway import ApiError, api_view, QueryOrBody
from users.models import Identity, Report


@scope_required("write:reports")
@api_view.post
def file_report(
    request,
    account_id: QueryOrBody[str],
    status_ids: QueryOrBody[list[str]] = [],
    comment: QueryOrBody[str] = "",
    forward: QueryOrBody[bool] = False,
    category: QueryOrBody[str] = "other",
    **kwargs,
) -> schemas.Report:
    subject_identity = get_object_or_404(Identity, pk=account_id)
    if not status_ids:
        raise ApiError(422, "Not status ids provided")
    subject_post = Post.objects.filter(id__in=status_ids).first()
    if not subject_post:
        raise ApiError(422, "Not status matched")
    r = Report.objects.create(
        subject_identity=subject_identity,
        subject_post=subject_post,
        source_domain=request.identity.domain,
        source_identity=request.identity,
        type=category,
        complaint=comment or category or "",
        forward=forward,
    )
    return r.to_mastodon_json()
