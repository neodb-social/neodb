from typing import Any, Literal

from django.conf import settings
from ninja import Schema, Status
from ninja.schema import Field

from common.api import NOT_FOUND, OK, OptionalOAuthAccessTokenAuth, Result, api
from mastodon.models import SocialAccount
from users.models import APIdentity, Webhook
from users.models.webhook import (
    MAX_WEBHOOKS_PER_USER,
    WebhookLimitReached,
    remove_webhook,
    scope_set,
    set_webhook,
    validate_webhook_url,
)


class TokenSchema(Schema):
    active: bool


class ExternalAccountSchema(Schema):
    platform: str
    handle: str
    url: str | None


class UserIdentitySchema(Schema):
    """Public info of an identity, embeddable in other schemas (e.g. as owner)."""

    username: str
    url: str
    display_name: str
    avatar: str

    @staticmethod
    def resolve_username(obj: "APIdentity | dict[str, Any]") -> str:
        # serialized either from an APIdentity ("user" local / "user@site"
        # remote, same form /api/user/{handle} accepts) or a prebuilt dict
        if isinstance(obj, dict):
            username = obj.get("username")
            return username if isinstance(username, str) else ""
        return obj.handle

    @staticmethod
    def resolve_avatar(obj: "APIdentity | dict[str, Any]") -> str:
        avatar = obj.get("avatar") if isinstance(obj, dict) else obj.avatar
        if not isinstance(avatar, str):
            return ""
        # image assets are absolute in the API (like cover_image_url), while
        # in-site page urls stay relative
        if avatar.startswith("/"):
            return settings.SITE_INFO["site_url"] + avatar
        return avatar


class UserSchema(UserIdentitySchema):
    external_acct: str | None = Field(deprecated=True)
    external_accounts: list[ExternalAccountSchema]
    roles: list[Literal["admin", "staff"]]


class WebhookSchema(Schema):
    url: str
    disabled: bool


class WebhookInSchema(Schema):
    url: str


class PreferenceSchema(Schema):
    default_crosspost: bool = Field(alias="mastodon_default_repost")
    default_visibility: int
    hidden_categories: list[str]
    language: str = Field(alias="user.language")


@api.get(
    "/token",
    response={200: TokenSchema},
    summary="Get token info",
    tags=["user"],
)
def token(request):
    return Status(200, {"active": request.auth is not None})


@api.get(
    "/me",
    response={200: UserSchema, 401: Result},
    summary="Get current user's basic info",
    tags=["user"],
)
def me(request):
    accts = SocialAccount.objects.filter(user=request.user)
    return Status(
        200,
        {
            # "id": str(request.user.identity.pk),
            "username": request.user.username,
            # site-relative, like identity urls elsewhere in the API
            "url": request.user.url,
            "external_acct": (
                request.user.mastodon.handle if request.user.mastodon else None
            ),
            "external_accounts": accts,
            "display_name": request.user.display_name,
            "avatar": request.user.avatar,
            "roles": request.user.get_roles(),
        },
    )


@api.get(
    "/me/preference",
    response={200: PreferenceSchema, 401: Result},
    summary="Get current user's preference",
    tags=["user"],
)
def preference(request):
    return Status(200, request.user.preference)


@api.get(
    "/me/webhook",
    response={200: WebhookSchema, 401: Result, 404: Result},
    summary="Get this application's webhook for the current user",
    tags=["user"],
)
def get_webhook(request):
    """
    Each application (the one this access token belongs to) may register one
    webhook URL per user. Changes to the user's marks, reviews, notes,
    collections and articles are POSTed to it as a small JSON payload:
    `{"type": "mark", "action": "save", "url": "...", "title": "..."}`.
    The payload is a trigger only; fetch details via the API.
    `disabled` becomes true after repeated delivery failures.
    """
    webhook = Webhook.objects.filter(
        user=request.user, application_id=request.application_id
    ).first()
    if not webhook:
        return NOT_FOUND
    return Status(200, webhook)


@api.put(
    "/me/webhook",
    response={200: WebhookSchema, 400: Result, 401: Result, 403: Result},
    summary="Set this application's webhook for the current user",
    tags=["user"],
)
def put_webhook(request, w_in: WebhookInSchema):
    """
    Register or replace the webhook URL. Only https URLs resolving to public
    addresses are accepted. Setting it again re-enables a disabled webhook.
    Requires the `push` scope as well; a user can have at most 5 webhooks
    across applications.
    """
    if "push" not in scope_set(getattr(request, "token_scopes", None)):
        return Status(403, {"message": "push scope required"})
    url = w_in.url.strip()
    if not validate_webhook_url(url):
        return Status(400, {"message": "Invalid webhook URL"})
    try:
        webhook = set_webhook(request.user, request.application_id, url)
    except WebhookLimitReached:
        return Status(
            403, {"message": f"At most {MAX_WEBHOOKS_PER_USER} webhooks per user"}
        )
    return Status(200, webhook)


@api.delete(
    "/me/webhook",
    response={200: Result, 401: Result},
    summary="Remove this application's webhook for the current user",
    tags=["user"],
)
def delete_webhook(request):
    remove_webhook(request.user.pk, request.application_id)
    return OK


@api.get(
    "/user/{handle}",
    response={200: UserSchema, 401: Result, 403: Result, 404: Result},
    tags=["user"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def user(request, handle: str):
    """
    Get user's basic info

    More detailed info can be fetched from Mastodon API

    Anonymous access is allowed, unless the identity has opted out of being
    viewed without login. Everything under `/user/{handle}` still needs a
    token.
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        return NOT_FOUND
    viewer = request.user.identity if request.user.is_authenticated else None
    if not viewer:
        # same gate as the web profile page and the piece queries: an identity
        # that opted out of anonymous viewing is not served to a token-less
        # caller either
        if not target.anonymous_viewable:
            return Status(401, {"message": "Login required"})
    elif target.is_blocking(viewer) or target.is_blocked_by(viewer):
        return Status(403, {"message": "unavailable"})
    return Status(
        200,
        {
            "username": target.handle,
            "url": target.url,
            "external_acct": None,
            "external_accounts": [],
            "display_name": target.display_name,
            "avatar": target.avatar,
            "roles": target.user.get_roles() if target.local else [],
        },
    )
