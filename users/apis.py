from typing import Literal

from django.conf import settings
from ninja import Schema
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


class UserSchema(Schema):
    url: str
    external_acct: str | None = Field(deprecated=True)
    external_accounts: list[ExternalAccountSchema]
    display_name: str
    avatar: str
    username: str
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
    return 200, {"active": request.auth is not None}


@api.get(
    "/me",
    response={200: UserSchema, 401: Result},
    summary="Get current user's basic info",
    tags=["user"],
)
def me(request):
    accts = SocialAccount.objects.filter(user=request.user)
    return 200, {
        # "id": str(request.user.identity.pk),
        "username": request.user.username,
        "url": settings.SITE_INFO["site_url"] + request.user.url,
        "external_acct": (
            request.user.mastodon.handle if request.user.mastodon else None
        ),
        "external_accounts": accts,
        "display_name": request.user.display_name,
        "avatar": request.user.avatar,
        "roles": request.user.get_roles(),
    }


@api.get(
    "/me/preference",
    response={200: PreferenceSchema, 401: Result},
    summary="Get current user's preference",
    tags=["user"],
)
def preference(request):
    return 200, request.user.preference


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
        return 403, {"message": "unavailable"}
    return 200, {
        "username": target.handle,
        "url": target.url,
        "external_acct": None,
        "external_accounts": [],
        "display_name": target.display_name,
        "avatar": target.avatar,
        "roles": target.user.get_roles() if target.local else [],
    }
