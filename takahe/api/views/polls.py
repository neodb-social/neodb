from django.http import Http404
from hatchway import ApiError, api_view, QueryOrBody

from activities.models import Post, PostInteraction
from users.models import Block
from api import schemas
from api.decorators import scope_required
from api.views.statuses import post_for_id


def poll_for_id(request, id: str) -> Post:
    """
    Returns the poll post for an ID, respecting visibility and deletion.
    """
    post = post_for_id(request, id)
    if post.type != Post.Types.question:
        raise Http404
    return post


@scope_required("read:statuses")
@api_view.get
def get_poll(request, id: str) -> schemas.Poll:
    post = poll_for_id(request, id)
    if Block.maybe_get(
        source=post.author, target=request.identity, require_active=True
    ):
        raise Http404
    post = post.refresh_question_if_stale()
    return schemas.Poll.from_post(post, identity=request.identity)


@scope_required("write:statuses")
@api_view.post
def vote_poll(request, id: str, choices: QueryOrBody[list[int]]) -> schemas.Poll:
    post = poll_for_id(request, id)
    try:
        PostInteraction.create_votes(post, request.identity, choices)
    except ValueError as e:
        raise ApiError(422, str(e)) from e
    post.refresh_from_db()
    return schemas.Poll.from_post(post, identity=request.identity)
