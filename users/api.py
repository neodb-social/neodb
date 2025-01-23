from ninja import Schema
from ninja.schema import Field

from common.api import *
from mastodon.models.common import SocialAccount


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


@api.get(
    "/me",
    response={200: UserSchema, 401: Result},
    summary="Get current user's basic info",
    tags=["user"],
)
def me(request):
    accts = SocialAccount.objects.filter(user=request.user)
    return 200, {
        "username": request.user.username,
        "url": settings.SITE_INFO["site_url"] + request.user.url,
        "external_acct": (
            request.user.mastodon.handle if request.user.mastodon else None
        ),
        "external_accounts": accts,
        "display_name": request.user.display_name,
        "avatar": request.user.avatar,
    }


@api.get(
    "/user/{handle}",
    response={200: UserSchema, 401: Result, 403: Result},
    tags=["user"],
)
def user(request, handle: str):
    """
    Get user's basic info

    More detailed info can be fetched from Mastodon API
    """
    target = APIdentity.get_by_handle(handle)
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
    }
