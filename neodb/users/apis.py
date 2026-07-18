from typing import Any, Literal

from django.conf import settings
from ninja import Schema, Status
from ninja.schema import Field

from common.api import NOT_FOUND, Result, api
from mastodon.models import SocialAccount
from users.models import APIdentity


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
    "/user/{handle}",
    response={200: UserSchema, 401: Result, 403: Result, 404: Result},
    tags=["user"],
)
def user(request, handle: str):
    """
    Get user's basic info

    More detailed info can be fetched from Mastodon API
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        return NOT_FOUND
    viewer = request.user.identity
    if target.is_blocking(viewer) or target.is_blocked_by(viewer):
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
