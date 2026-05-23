from typing import Literal, Optional, Union

from activities import models as activities_models
from core.html import FediverseHtmlParser
from hatchway import Field, Schema
from users.services import IdentityService

from api import models as api_models
from users import models as users_models


class Application(Schema):
    id: str
    name: str
    website: str | None = None
    client_id: str
    client_secret: str
    redirect_uri: str
    redirect_uris: list[str]
    vapid_key: str | None = None

    @classmethod
    def from_application(cls, application: api_models.Application) -> "Application":
        a = application.to_mastodon_json()
        a["redirect_uri"] = a["redirect_uris"]
        a["redirect_uris"] = [a["redirect_uri"]]
        return cls(**a)

    @classmethod
    def from_application_no_keys(
        cls, application: api_models.Application
    ) -> "Application":
        return cls(**application.to_mastodon_json(include_client_keys=False))


class CustomEmoji(Schema):
    shortcode: str
    url: str
    static_url: str
    visible_in_picker: bool
    category: str

    @classmethod
    def from_emoji(cls, emoji: activities_models.Emoji) -> "CustomEmoji":
        return cls(**emoji.to_mastodon_json())


class AccountField(Schema):
    name: str
    value: str
    verified_at: str | None = None


class Account(Schema):
    id: str
    username: str
    acct: str
    url: str
    uri: str
    display_name: str
    note: str
    avatar: str
    avatar_static: str
    header: str | None = Field(...)
    header_static: str | None = Field(...)
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
    last_status_at: str | None = Field(...)
    statuses_count: int
    followers_count: int
    following_count: int
    hide_collections: bool = False
    source: dict | None = None

    @classmethod
    def from_identity(
        cls,
        identity: users_models.Identity,
        source=False,
    ) -> "Account":
        return cls(**identity.to_mastodon_json(source=source))


class MediaAttachment(Schema):
    id: str
    type: Literal["unknown", "image", "gifv", "video", "audio"]
    url: str
    preview_url: str | None = None
    remote_url: str | None = None
    meta: dict
    description: str | None = None
    blurhash: str | None = None

    @classmethod
    def from_post_attachment(
        cls, attachment: activities_models.PostAttachment
    ) -> "MediaAttachment":
        return cls(**attachment.to_mastodon_json())


class PollOptions(Schema):
    title: str
    votes_count: int | None = None


class Poll(Schema):
    id: str
    expires_at: str | None = None
    expired: bool
    multiple: bool
    votes_count: int
    voters_count: int | None = None
    voted: bool
    own_votes: list[int]
    options: list[PollOptions]
    emojis: list[CustomEmoji]

    @classmethod
    def from_post(
        cls,
        post: activities_models.Post,
        identity: users_models.Identity | None = None,
    ) -> "Poll":
        return cls(**post.type_data.to_mastodon_json(post, identity=identity))


class StatusMention(Schema):
    id: str
    username: str
    url: str
    acct: str


class StatusTag(Schema):
    name: str
    url: str


class StatusApplication(Schema):
    name: str | None
    website: str | None


class PreviewCard(Schema):
    url: str
    title: str
    description: str
    type: Literal["link", "photo", "video", "rich"]
    author_name: str
    author_url: str
    provider_name: str
    provider_url: str
    html: str
    width: int
    height: int
    image: str | None
    embed_url: str
    blurhash: str | None


class Status(Schema):
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
    reblog: Optional["Status"] = Field(...)
    quote: dict | None = None
    quote_id: str | None = None
    quoted_status_id: str | None = None
    poll: Poll | None = Field(...)
    card: PreviewCard | None = Field(...)
    language: str | None = Field(...)
    text: str | None = Field(...)
    edited_at: str | None = None
    favourited: bool = False
    reblogged: bool = False
    muted: bool = False
    bookmarked: bool = False
    pinned: bool = False
    application: StatusApplication | None = None
    ext_neodb: dict = None

    @classmethod
    def from_post(
        cls,
        post: activities_models.Post,
        interactions: dict[str, set[str]] | None = None,
        bookmarks: set[str] | None = None,
        identity: users_models.Identity | None = None,
    ) -> "Status":
        return cls(
            **post.to_mastodon_json(
                interactions=interactions,
                bookmarks=bookmarks,
                identity=identity,
            )
        )

    @classmethod
    def map_from_post(
        cls,
        posts: list[activities_models.Post],
        identity: users_models.Identity,
    ) -> list["Status"]:
        interactions = activities_models.PostInteraction.get_post_interactions(
            posts, identity
        )
        bookmarks = users_models.Bookmark.for_identity(identity, posts)
        return [
            cls.from_post(
                post,
                interactions=interactions,
                bookmarks=bookmarks,
                identity=identity,
            )
            for post in posts
        ]

    @classmethod
    def from_timeline_event(
        cls,
        timeline_event: activities_models.TimelineEvent,
        interactions: dict[str, set[str]] | None = None,
        bookmarks: set[str] | None = None,
        identity: users_models.Identity | None = None,
    ) -> "Status":
        return cls(
            **timeline_event.to_mastodon_status_json(
                interactions=interactions, bookmarks=bookmarks, identity=identity
            )
        )

    @classmethod
    def map_from_timeline_event(
        cls,
        events: list[activities_models.TimelineEvent],
        identity: users_models.Identity,
    ) -> list["Status"]:
        interactions = activities_models.PostInteraction.get_event_interactions(
            events, identity
        )
        bookmarks = users_models.Bookmark.for_identity(
            identity, events, "subject_post_id"
        )
        return [
            cls.from_timeline_event(
                event, interactions=interactions, bookmarks=bookmarks, identity=identity
            )
            for event in events
        ]


class StatusSource(Schema):
    id: str
    text: str
    spoiler_text: str

    @classmethod
    def from_post(cls, post: activities_models.Post):
        return cls(
            id=str(post.pk),
            text=FediverseHtmlParser(post.content).plain_text,
            spoiler_text=post.summary or "",
        )


class Conversation(Schema):
    id: str
    unread: bool
    accounts: list[Account]
    last_status: Status | None = Field(...)

    @classmethod
    def from_conversation(
        cls,
        conversation: "activities_models.Conversation",
        identity: "users_models.Identity",
    ) -> "Conversation":
        from activities.models.conversation import ConversationMembership

        try:
            membership = ConversationMembership.objects.get(
                conversation=conversation, identity=identity
            )
            unread = membership.unread
        except ConversationMembership.DoesNotExist:
            unread = False
        other_accounts = [
            Account.from_identity(p)
            for p in conversation.participants.all()
            if p.pk != identity.pk
        ]
        last_status = None
        if conversation.last_post:
            last_status = Status.from_post(conversation.last_post, identity=identity)
        return cls(
            id=str(conversation.pk),
            unread=unread,
            accounts=other_accounts,
            last_status=last_status,
        )


class Notification(Schema):
    id: str
    type: Literal[
        "mention",
        "status",
        "reblog",
        "follow",
        "follow_request",
        "favourite",
        "poll",
        "update",
        "quote",
        "admin.sign_up",
        "admin.report",
    ]
    group_key: str
    created_at: str
    account: Account
    status: Status | None = None

    @classmethod
    def from_timeline_event(
        cls,
        event: activities_models.TimelineEvent,
        interactions=None,
    ) -> "Notification":
        return cls(**event.to_mastodon_notification_json(interactions=interactions))


class NotificationPolicySummary(Schema):
    pending_requests_count: int
    pending_notifications_count: int


class NotificationPolicy(Schema):
    for_not_following: Literal["accept", "filter", "drop"]
    for_not_followers: Literal["accept", "filter", "drop"]
    for_new_accounts: Literal["accept", "filter", "drop"]
    for_private_mentions: Literal["accept", "filter", "drop"]
    for_limited_accounts: Literal["accept", "filter", "drop"]
    summary: NotificationPolicySummary

    @classmethod
    def from_identity(cls, identity) -> "NotificationPolicy":
        cfg = identity.config_identity
        return cls(
            for_not_following=cfg.notification_policy_not_following,
            for_not_followers=cfg.notification_policy_not_followers,
            for_new_accounts=cfg.notification_policy_new_accounts,
            for_private_mentions=cfg.notification_policy_private_mentions,
            for_limited_accounts=cfg.notification_policy_limited_accounts,
            summary=NotificationPolicySummary(
                pending_requests_count=0,
                pending_notifications_count=0,
            ),
        )


class Tag(Schema):
    name: str
    url: str
    history: list
    following: bool | None = None

    @classmethod
    def from_hashtag(
        cls,
        hashtag: activities_models.Hashtag,
        following: bool | None = None,
        domain: users_models.Domain | None = None,
    ) -> "Tag":
        return cls(**hashtag.to_mastodon_json(following=following, domain=domain))

    @classmethod
    def map_from_hashtags(
        cls,
        hashtags: list[activities_models.Hashtag],
        domain: users_models.Domain | None = None,
        identity: users_models.Identity | None = None,
    ) -> list["Tag"]:
        following = set()
        if identity:
            following.update(
                identity.hashtag_follows.values_list("hashtag_id", flat=True)
            )
        return [
            cls.from_hashtag(tag, following=tag.pk in following, domain=domain)
            for tag in hashtags
        ]

    @classmethod
    def map_from_names(
        cls,
        tag_names: list[str],
        domain: users_models.Domain | None = None,
        identity: users_models.Identity | None = None,
    ) -> list["Tag"]:
        return cls.map_from_hashtags(
            activities_models.Hashtag.objects.filter(hashtag__in=tag_names),
            domain=domain,
            identity=identity,
        )


class FollowedTag(Tag):
    id: str

    @classmethod
    def from_follow(
        cls,
        follow: users_models.HashtagFollow,
    ) -> "FollowedTag":
        return cls(id=str(follow.id), **follow.hashtag.to_mastodon_json(following=True))

    @classmethod
    def map_from_follows(
        cls,
        hashtag_follows: list[users_models.HashtagFollow],
    ) -> list["Tag"]:
        return [cls.from_follow(follow) for follow in hashtag_follows]


class FeaturedTag(Schema):
    id: str
    name: str
    url: str
    statuses_count: int
    last_status_at: str

    @classmethod
    def from_feature(
        cls,
        feature: users_models.HashtagFeature,
        domain: users_models.Domain | None = None,
    ) -> "FeaturedTag":
        return cls(**feature.to_mastodon_json(domain=domain))


class Search(Schema):
    accounts: list[Account]
    statuses: list[Status]
    hashtags: list[Tag]


class Relationship(Schema):
    id: str
    following: bool
    followed_by: bool
    showing_reblogs: bool
    notifying: bool
    languages: list[str]
    blocking: bool
    blocked_by: bool
    muting: bool
    muting_notifications: bool
    requested: bool
    domain_blocking: bool
    endorsed: bool
    note: str

    @classmethod
    def from_identity_pair(
        cls,
        identity: users_models.Identity,
        from_identity: users_models.Identity,
    ) -> "Relationship":
        return cls(
            **IdentityService(identity).mastodon_json_relationship(from_identity)
        )


class Context(Schema):
    ancestors: list[Status]
    descendants: list[Status]


class FamiliarFollowers(Schema):
    id: str
    accounts: list[Account]


class Announcement(Schema):
    id: str
    content: str
    starts_at: str | None = Field(...)
    ends_at: str | None = Field(...)
    all_day: bool
    published_at: str
    updated_at: str
    read: bool | None = None  # Only missing for anonymous responses
    mentions: list[Account]
    statuses: list[Status]
    tags: list[Tag]
    emojis: list[CustomEmoji]
    reactions: list

    @classmethod
    def from_announcement(
        cls,
        announcement: users_models.Announcement,
        user: users_models.User,
    ) -> "Announcement":
        return cls(**announcement.to_mastodon_json(user=user))


class List(Schema):
    id: str
    title: str
    replies_policy: Literal["followed", "list", "none"]
    exclusive: bool

    @classmethod
    def from_list(
        cls,
        list_instance: users_models.List,
    ) -> "List":
        return cls(**list_instance.to_mastodon_json())


class Preferences(Schema):
    posting_default_visibility: Literal[
        "public",
        "unlisted",
        "private",
        "direct",
    ] = Field(alias="posting:default:visibility")
    posting_default_sensitive: bool = Field(alias="posting:default:sensitive")
    posting_default_language: str | None = Field(alias="posting:default:language")
    reading_expand_media: Literal[
        "default",
        "show_all",
        "hide_all",
    ] = Field(alias="reading:expand:media")
    reading_expand_spoilers: bool = Field(alias="reading:expand:spoilers")
    reading_autoplay_gifs: bool = Field(alias="reading:autoplay:gifs")

    @classmethod
    def from_identity(
        cls,
        identity: users_models.Identity,
    ) -> "Preferences":
        visibility_mapping = {
            activities_models.Post.Visibilities.public: "public",
            activities_models.Post.Visibilities.unlisted: "unlisted",
            activities_models.Post.Visibilities.followers: "private",
            activities_models.Post.Visibilities.mentioned: "direct",
            activities_models.Post.Visibilities.local_only: "public",
        }
        preferred_posting_language = None
        if identity.config_identity.preferred_posting_language != "":
            preferred_posting_language = (
                identity.config_identity.preferred_posting_language
            )

        return cls.model_validate(
            {
                "posting:default:visibility": visibility_mapping[
                    identity.config_identity.default_post_visibility
                ],
                "posting:default:sensitive": False,
                "posting:default:language": preferred_posting_language,
                "reading:expand:media": "default",
                "reading:expand:spoilers": identity.config_identity.expand_content_warnings,
                "reading:autoplay:gifs": True,
            }
        )


class PushSubscriptionKeys(Schema):
    p256dh: str
    auth: str


class PushSubscriptionCreation(Schema):
    endpoint: str
    keys: PushSubscriptionKeys


class PushDataAlerts(Schema):
    mention: bool = False
    status: bool = False
    reblog: bool = False
    follow: bool = False
    follow_request: bool = False
    favourite: bool = False
    poll: bool = False
    update: bool = False
    admin_sign_up: bool = Field(False, alias="admin.sign_up")
    admin_report: bool = Field(False, alias="admin.report")


class PushData(Schema):
    alerts: PushDataAlerts
    policy: Literal["all", "followed", "follower", "none"] = "all"


class PushSubscription(Schema):
    id: int  # This should be str, but some clients (IceCubes) expect an int
    endpoint: str
    alerts: PushDataAlerts
    policy: str
    server_key: str

    @classmethod
    def from_token(
        cls,
        token: api_models.Token,
    ) -> Optional["PushSubscription"]:
        try:
            return cls(**token.push_subscription.to_mastodon_json())
        except api_models.PushSubscription.DoesNotExist:
            return None


class Marker(Schema):
    last_read_id: str
    version: int
    updated_at: str

    @classmethod
    def from_marker(
        cls,
        marker: users_models.Marker,
    ) -> "Marker":
        return cls(**marker.to_mastodon_json())


class Report(Schema):
    id: str
    action_taken: bool = False
    action_taken_at: str | None = None
    category: str = "other"
    comment: str
    forwarded: bool = False
    created_at: str
    status_ids: list[int]
    rule_ids: list[int] | None = None
    target_account: Account
