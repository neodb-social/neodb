from ninja import Schema
from common.api import *
from oauth2_provider.decorators import protected_resource
from ninja.security import django_auth


class UserSchema(Schema):
    external_acct: str
    display_name: str
    avatar: str


@api.get(
    "/me",
    response={200: UserSchema, 400: Result, 403: Result},
    summary="Get current user's basic info",
)
@protected_resource()
def me(request):
    return 200, {
        "external_acct": request.user.mastodon_username,
        "display_name": request.user.display_name,
        "avatar": request.user.mastodon_account.get("avatar"),
    }
