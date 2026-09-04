from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils.functional import lazy
from loguru import logger
from ninja import Field, NinjaAPI, Schema, Status
from pydantic import AliasChoices
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination
from ninja.security import HttpBearer

from catalog.models import Item
from common.models import SiteConfig
from takahe.utils import Takahe
from users.models.apidentity import APIdentity

PERMITTED_WRITE_METHODS = ["PUT", "POST", "DELETE", "PATCH"]
PERMITTED_READ_METHODS = ["GET", "HEAD", "OPTIONS"]


class OAuthAccessTokenAuth(HttpBearer):
    def authenticate(self, request, token) -> bool:
        if not token:
            logger.debug("API auth: no access token provided")
            return False
        tk = Takahe.get_token(token)
        if not tk:
            logger.debug("API auth: access token not found")
            return False
        if tk.revoked:
            logger.debug("API auth: access token revoked")
            return False
        request_scope = ""
        request_method = request.method
        if request_method in PERMITTED_READ_METHODS:
            request_scope = "read"
        elif request_method in PERMITTED_WRITE_METHODS:
            request_scope = "write"
        else:
            logger.debug("API auth: unsupported HTTP method")
            return False
        if request_scope not in tk.scopes:
            logger.debug("API auth: scope not allowed")
            return False
        identity = APIdentity.objects.filter(pk=tk.identity_id).first()
        if not identity:
            logger.debug("API auth: identity not found")
            return False
        if identity.deleted:
            logger.debug("API auth: identity deleted")
            return False
        user = identity.user
        if not user:
            logger.debug("API auth: user not found")
            return False
        request.user = user
        request.identity_id = tk.identity_id
        request.application_id = tk.application_id
        return True


class OptionalOAuthAccessTokenAuth(OAuthAccessTokenAuth):
    """Auth that processes Bearer token if present, but allows anonymous access."""

    def __call__(self, request: HttpRequest) -> bool:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return True  # No token provided, allow anonymous access
        return super().__call__(request) or False


class EmptyResult(Schema):
    pass


class Result(Schema):
    message: str | None
    # error: Optional[str]


class RedirectedResult(Schema):
    message: str | None
    url: str


def renamed_field(new: str, old: str) -> Any:
    """Field that accepts either name of a renamed input field.

    For the in-schemas whose text field was renamed (`body` -> `content`,
    `brief` -> `description`). `new` comes first, so it wins when a caller
    sends both, and it is the name the OpenAPI request schema requires;
    declare `old` alongside as `deprecated_field()` so it stays visible.

    A model validator cannot do this: ninja's own root validator is
    mode="wrap" and hands subclass validators a DjangoGetter, not the payload.
    """
    return Field(validation_alias=AliasChoices(new, old))


def deprecated_field() -> Any:
    """The old name of a `renamed_field`, kept only to document it.

    Its value is read through the new field's alias, so views never read
    this one; it exists so the Swagger page still lists the name older
    clients send.
    """
    return Field(None, deprecated=True)


class PageNumberPagination(NinjaPageNumberPagination):
    items_attribute = "data"

    class Output(Schema):
        data: list[Any]
        pages: int
        count: int

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: NinjaPageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        if isinstance(queryset, dict):
            # ninja unwraps Status(code, payload) before paginating, so a view
            # returning e.g. Status(302, {"message": ...}) lands here as a bare
            # payload: pass it through with the items attribute ninja expects.
            return {self.items_attribute: [], "count": 0, "pages": 0, **queryset}
        val = super().paginate_queryset(queryset, pagination, request, **params)
        return {
            "data": val["data"],
            "count": val["count"],
            "pages": (val["count"] + self.page_size - 1) // self.page_size,
        }


def _site_name() -> str:
    SiteConfig.ensure_loaded()
    return SiteConfig.system.site_name


def _api_description() -> str:
    site_url = settings.SITE_INFO["site_url"]
    return f"{_site_name()} API <hr/><a href='{site_url}'>Learn more</a>"


api = NinjaAPI(
    auth=OAuthAccessTokenAuth(),
    # lazy strings: the site name is a runtime SiteConfig value, and this module
    # is imported before any request has loaded the config
    title=lazy(lambda: f"{_site_name()} API", str)(),
    version="1.0.0",
    description=lazy(_api_description, str)(),
)

NOT_FOUND = Status(404, {"message": "Not found"})
OK = Status(200, {"message": "OK"})
NO_DATA = {"data": [], "count": 0, "pages": 0}
INVALID_PAGE = Status(400, {"message": "Invalid page number"})


def _resolve_item(
    item_uuid: str, api_path: str, response: HttpResponse, redirect_status: int
) -> tuple[Item | None, Any]:
    item = Item.get_by_url(item_uuid)
    if not item or item.is_deleted:
        return None, Status(404, {"message": "Item not found"})
    if item.merged_to_item:
        url = api_path.format(uuid=item.final_item.uuid)
        response["Location"] = url
        return None, Status(redirect_status, {"message": "Item merged", "url": url})
    return item, None


def resolve_item_for_read(
    item_uuid: str, api_path: str, response: HttpResponse
) -> tuple[Item | None, Any]:
    """Resolve the item a read API is about, redirecting merged items.

    `api_path` is this endpoint's path with `{uuid}` where the item uuid goes.
    Returns `(item, None)` when the item can be read, otherwise `(None, status)`:
    404 if the item is unknown or deleted, or 302 pointing at the item a merged
    one was folded into.
    """
    return _resolve_item(item_uuid, api_path, response, 302)


def resolve_item_for_write(
    item_uuid: str, api_path: str, response: HttpResponse
) -> tuple[Item | None, Any]:
    """Resolve the item a write API is about, redirecting merged items.

    Same as `resolve_item_for_read`, but a merged item gets 307 rather than 302,
    so the client replays the same method and body instead of turning the write
    into a GET.
    """
    return _resolve_item(item_uuid, api_path, response, 307)
