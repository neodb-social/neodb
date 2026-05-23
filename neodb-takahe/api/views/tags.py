import datetime

from django.http import HttpRequest
from api.views import get_object_or_404
from django.utils import timezone

from activities.models import FanOut, Hashtag, Post
from api import schemas
from api.decorators import scope_required
from api.pagination import MastodonPaginator, PaginatingApiResponse, PaginationResult
from hatchway import QueryOrBody, api_view
from users.models import HashtagFollow


@api_view.get
def hashtag(request: HttpRequest, hashtag: str) -> schemas.Tag:
    tag = get_object_or_404(
        Hashtag,
        pk=hashtag.lower(),
    )
    following = None
    if request.identity:
        following = tag.followers.filter(identity=request.identity).exists()

    return schemas.Tag.from_hashtag(
        tag,
        following=following,
        domain=request.domain,
    )


@scope_required("read:follows")
@api_view.get
def followed_tags(
    request: HttpRequest,
    max_id: str | None = None,
    since_id: str | None = None,
    min_id: str | None = None,
    limit: int = 100,
) -> list[schemas.Tag]:
    queryset = HashtagFollow.objects.by_identity(request.identity)
    paginator = MastodonPaginator()
    pager: PaginationResult[HashtagFollow] = paginator.paginate(
        queryset,
        min_id=min_id,
        max_id=max_id,
        since_id=since_id,
        limit=limit,
    )
    return PaginatingApiResponse(
        schemas.FollowedTag.map_from_follows(pager.results),
        request=request,
        include_params=["limit"],
    )


@scope_required("write:follows")
@api_view.post
def follow(
    request: HttpRequest,
    id: str,
) -> schemas.Tag:
    hashtag = get_object_or_404(
        Hashtag,
        pk=id.lower(),
    )
    request.identity.hashtag_follows.get_or_create(hashtag=hashtag)
    return schemas.Tag.from_hashtag(
        hashtag,
        following=True,
        domain=request.domain,
    )


@scope_required("write:follows")
@api_view.post
def unfollow(
    request: HttpRequest,
    id: str,
) -> schemas.Tag:
    hashtag = get_object_or_404(
        Hashtag,
        pk=id.lower(),
    )
    request.identity.hashtag_follows.filter(hashtag=hashtag).delete()
    return schemas.Tag.from_hashtag(
        hashtag,
        following=False,
        domain=request.domain,
    )


@scope_required("read:accounts")
@api_view.get
def featured_tags(request) -> list[schemas.FeaturedTag]:
    return [
        schemas.FeaturedTag.from_feature(f, domain=request.domain)
        for f in request.identity.hashtag_features.select_related("hashtag")
    ]


@scope_required("write:accounts")
@api_view.post
def feature_tag(request, name: QueryOrBody[str]) -> schemas.FeaturedTag:
    tag = Hashtag.ensure_hashtag(name)
    feature, created = request.identity.hashtag_features.get_or_create(hashtag=tag)
    if created:
        for target in feature.get_targets():
            FanOut.objects.create(
                type=FanOut.Types.tag_featured,
                identity=target,
                subject_identity=feature.identity,
                subject_hashtag=feature.hashtag,
            )
    return schemas.FeaturedTag.from_feature(feature, domain=request.domain)


@scope_required("write:accounts")
@api_view.delete
def unfeature_tag(request, id: str) -> dict:
    for feature in request.identity.hashtag_features.filter(pk=id):
        for target in feature.get_targets():
            FanOut.objects.create(
                type=FanOut.Types.tag_unfeatured,
                identity=target,
                subject_identity=feature.identity,
                subject_hashtag=feature.hashtag,
            )
        feature.delete()
    return {}


@scope_required("read:accounts")
@api_view.get
def featured_tag_suggestions(request) -> list[schemas.Tag]:
    since = timezone.now() - datetime.timedelta(days=7)
    recent_tags = []
    for tags in (
        Post.objects.not_hidden()
        .filter(author=request.identity, created__gte=since, hashtags__isnull=False)
        .values_list("hashtags", flat=True)
    ):
        recent_tags.extend([t for t in tags if t not in recent_tags])
    return schemas.Tag.map_from_names(recent_tags, domain=request.domain)
