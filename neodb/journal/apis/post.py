from typing import Literal, Union

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.db.models.expressions import BaseExpression
from django.http import HttpResponse
from ninja import Field, Schema

from catalog.models import Item
from common.api import (
    INVALID_PAGE,
    OptionalOAuthAccessTokenAuth,
    RedirectedResult,
    Result,
    api,
    resolve_item_for_read,
)
from journal.models import (
    Collection,
    CollectionMember,
    Comment,
    Note,
    Piece,
    PiecePost,
    Review,
    ShelfMember,
    q_piece_visible_to_user,
)
from takahe.models import Identity
from takahe.models import Post as TakahePost
from users.models import User

TIMELINE_LINK_MAX_LIMIT = 40
TIMELINE_LINK_DEFAULT_LIMIT = 20
ITEM_POSTS_PAGE_SIZE = 20


def _with_status_relations(posts: QuerySet) -> QuerySet:
    """Load every relation Post.to_mastodon_json reads, so a page of posts costs a fixed number of queries."""
    return posts.select_related(
        "author", "author__domain", "application"
    ).prefetch_related(
        "attachments",
        "emojis",
        Prefetch("mentions", queryset=Identity.objects.select_related("domain")),
    )


class CustomEmoji(Schema):
    shortcode: str
    url: str
    static_url: str
    visible_in_picker: bool
    category: str


class AccountField(Schema):
    name: str
    value: str
    verified_at: str | None = None


class Account(Schema):
    id: str
    username: str
    acct: str
    url: str
    display_name: str
    note: str
    avatar: str
    avatar_static: str
    header: str
    header_static: str
    locked: bool
    fields: list[AccountField]
    emojis: list[CustomEmoji]
    bot: bool
    group: bool
    discoverable: bool
    indexable: bool
    moved: Union[None, bool, "Account"] = None
    suspended: bool = False
    limited: bool = False
    created_at: str


class MediaAttachment(Schema):
    id: str
    type: Literal["unknown", "image", "gifv", "video", "audio"]
    url: str
    preview_url: str
    remote_url: str | None = None
    meta: dict
    description: str | None = None
    blurhash: str | None = None


class StatusMention(Schema):
    id: str
    username: str
    url: str
    acct: str


class StatusTag(Schema):
    name: str
    url: str


class StatusApplication(Schema):
    name: str | None = None
    website: str | None = None


class Post(Schema):
    id: str
    uri: str
    created_at: str
    account: Account
    content: str
    visibility: Literal["public", "unlisted", "private", "direct"]
    sensitive: bool
    spoiler_text: str
    media_attachments: list[MediaAttachment]
    mentions: list[StatusMention]
    tags: list[StatusTag]
    emojis: list[CustomEmoji]
    reblogs_count: int
    favourites_count: int
    replies_count: int
    url: str | None = Field(...)
    in_reply_to_id: str | None = Field(...)
    in_reply_to_account_id: str | None = Field(...)
    # reblog: Optional["Status"] = Field(...)
    # poll: Poll | None = Field(...)
    # card: None = Field(...)
    language: str | None = Field(...)
    text: str | None = Field(...)
    edited_at: str | None = None
    favourited: bool = False
    reblogged: bool = False
    muted: bool = False
    bookmarked: bool = False
    pinned: bool = False
    application: StatusApplication | None = None
    ext_neodb: dict | None = None


class PaginatedPostList(Schema):
    data: list[Post]
    pages: int
    count: int


PostTypes = {"mark", "comment", "review", "collection", "note"}


def _item_pieces(item: Item, types: set[str], user: User) -> QuerySet:
    """(pk, created_time) of the item's pieces of the given types visible to user.

    Mirrors the journal index docs: a comment with a sibling mark shares the
    mark's post and lives in the mark's doc, so it is reached through the
    mark and never on its own; that keeps one row per post. A post orphaned
    by a shelf change has no piece, so it never shows here.
    """
    visible = q_piece_visible_to_user(user)
    has_post = Exists(PiecePost.objects.filter(piece_id=OuterRef("pk")))
    on_item = Q(item_id=item.pk)
    sibling = {"owner_id": OuterRef("owner_id"), "item_id": item.pk}

    def pieces(model: type[Piece], *conditions: Q | BaseExpression) -> QuerySet:
        # plain QuerySet skips polymorphic loading and ShelfMember's annotations
        return (
            QuerySet(model)
            .filter(*conditions, visible, has_post)
            .values_list("pk", "created_time")
        )

    qs = []
    if "mark" in types:
        qs.append(pieces(ShelfMember, on_item))
    elif "comment" in types:
        qs.append(
            pieces(ShelfMember, on_item, Exists(QuerySet(Comment).filter(**sibling)))
        )
    if "comment" in types:
        qs.append(
            pieces(Comment, on_item, ~Exists(QuerySet(ShelfMember).filter(**sibling)))
        )
    if "review" in types:
        qs.append(pieces(Review, on_item))
    if "note" in types:
        qs.append(pieces(Note, on_item))
    if "collection" in types:
        members = CollectionMember.objects.filter(item_id=item.pk)
        qs.append(pieces(Collection, Q(pk__in=members.values("parent_id"))))
    return qs[0].union(*qs[1:], all=True) if len(qs) > 1 else qs[0]


def _latest_posts(piece_ids: list[int]) -> list[TakahePost]:
    """Each piece's latest post, in piece_ids order; pieces whose post is
    gone from takahe are skipped."""
    # latest link wins, as in Piece.latest_post_id
    post_ids = dict(
        PiecePost.objects.filter(piece_id__in=piece_ids)
        .order_by("piece_id", "-pk")
        .distinct("piece_id")
        .values_list("piece_id", "post_id")
    )
    posts = {
        p.pk: p
        for p in _with_status_relations(
            TakahePost.objects.filter(pk__in=post_ids.values()).exclude(
                state__in=["deleted", "deleted_fanned_out"]
            )
        )
    }
    return [posts[post_ids[pk]] for pk in piece_ids if post_ids.get(pk) in posts]


@api.get(
    "/item/{item_uuid}/posts/",
    response={
        200: PaginatedPostList,
        302: RedirectedResult,
        400: Result,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["catalog"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def list_posts_for_item(
    request,
    item_uuid: str,
    response: HttpResponse,
    type: str | None = None,
    page: int = 1,
):
    """
    Get posts for an item

    `type` is optional, can be a comma separated list of `comment`, `review`, `collection`, `note`, `mark`; default is `comment,review`

    Anonymous callers see only public posts from accounts that allow anonymous viewing.

    If the item was merged into another one, HTTP 302 is returned.
    """
    if page < 1 or page > 99:
        return INVALID_PAGE
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/item/{uuid}/posts/", response
    )
    if not item:
        return redirect
    types = {t for t in (type or "").split(",") if t in PostTypes}
    pieces = _item_pieces(item, types or {"comment", "review"}, request.user)
    total = pieces.count()
    offset = (page - 1) * ITEM_POSTS_PAGE_SIZE
    piece_ids = [
        pk
        for pk, _ in pieces.order_by("-created_time", "-pk")[
            offset : offset + ITEM_POSTS_PAGE_SIZE
        ]
    ]
    # a piece whose post takahe pruned or deleted still counts in `total`
    # but yields no entry in `data`
    return {
        "data": [p.to_mastodon_json() for p in _latest_posts(piece_ids)],
        "pages": (total + ITEM_POSTS_PAGE_SIZE - 1) // ITEM_POSTS_PAGE_SIZE,
        "count": total,
    }


# The one versioned path in an otherwise unversioned API: it fills in a
# timeline Mastodon defines but takahe does not serve, so it has to keep
# Mastodon's own path and its bare-list, `limit`-based contract.
@api.get(
    "/v1/timelines/link",
    response={200: list[Post], 401: Result},
    tags=["mastodon"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def timeline_link(
    request,
    url: str,
    limit: int = TIMELINE_LINK_DEFAULT_LIMIT,
) -> list[Post]:
    """
    Get statuses that contain a link to the given URL (Mastodon-compatible endpoint).

    Returns posts visible to the requesting user that are about the catalog item
    identified by `url`, which may be a NeoDB item URL or an external resource
    URL (e.g. a Douban or Goodreads page). Anonymous callers see only public posts
    from accounts that allow anonymous viewing.
    """
    limit = min(max(1, limit), TIMELINE_LINK_MAX_LIMIT)
    item = Item.get_by_remote_url(url)
    if not item:
        return []
    pieces = _item_pieces(item, PostTypes, request.user)
    piece_ids = [pk for pk, _ in pieces.order_by("-created_time", "-pk")[:limit]]
    return [p.to_mastodon_json() for p in _latest_posts(piece_ids)]
