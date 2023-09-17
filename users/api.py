from ninja import Schema
from ninja.security import django_auth
from oauth2_provider.decorators import protected_resource

from common.api import *


class UserSchema(Schema):
    url: str
    external_acct: str
    display_name: str
    avatar: str


@api.api_operation(
    ["GET", "OPTIONS"],
    "/me",
    response={200: UserSchema, 401: Result},
    summary="Get current user's basic info",
)
def me(request):
    return 200, {
        "url": settings.SITE_INFO["site_url"] + request.user.url,
        "external_acct": request.user.mastodon_acct,
        "display_name": request.user.display_name,
        "avatar": request.user.avatar,
    }
