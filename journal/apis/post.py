from typing import List, Literal, Union

from ninja import Field, Schema

from catalog.common.models import Item
from common.api import NOT_FOUND, Result, api
from journal.models.index import JournalIndex, JournalQueryParser


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
    # last_status_at: str | None = Field(...)
    # statuses_count: int | None
    # followers_count: int | None
    # following_count: int | None
    # source: dict | None = None


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
    ext_neodb: dict | None = None


class PaginatedPostList(Schema):
    data: List[Post]
    pages: int
    count: int


PostTypes = {"mark", "comment", "review", "collection", "note"}


@api.get(
    "/item/{item_uuid}/posts/",
    response={200: PaginatedPostList, 401: Result, 404: Result},
    tags=["catalog"],
)
def list_posts_for_item(request, item_uuid: str, type: str | None = None):
    """
    Get posts for an item

    `type` is optional, can be a comma separated list of `comment`, `review`, `collection`, `note`, `mark`; default is `comment,review`
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return NOT_FOUND
    types = [t for t in (type or "").split(",") if t in PostTypes]
    q = "type:" + ",".join(types or ["comment", "review"])
    query = JournalQueryParser(q)
    query.filter_by_viewer(request.user.identity)
    query.filter("item_id", item.pk)
    r = JournalIndex.instance().search(query)
    result = {
        "data": [
            p.to_mastodon_json()
            for p in r.posts.prefetch_related("attachments", "author")
        ],
        "pages": r.pages,
        "count": r.total,
    }
    return result
