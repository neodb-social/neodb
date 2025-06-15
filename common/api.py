from typing import Any, List

from django.conf import settings
from django.db.models import QuerySet
from loguru import logger
from ninja import NinjaAPI, Schema
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination
from ninja.security import HttpBearer

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
        request.identity_id = tk.identity_id  # type:ignore
        request.application_id = tk.application_id  # type:ignore
        return True


class EmptyResult(Schema):
    pass


class Result(Schema):
    message: str | None
    # error: Optional[str]


class RedirectedResult(Schema):
    message: str | None
    url: str


class PageNumberPagination(NinjaPageNumberPagination):
    items_attribute = "data"

    class Output(Schema):
        data: List[Any]
        pages: int
        count: int

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: NinjaPageNumberPagination.Input,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, **params)
        return {
            "data": val["items"],
            "count": val["count"],
            "pages": (val["count"] + self.page_size - 1) // self.page_size,
        }


api = NinjaAPI(
    auth=OAuthAccessTokenAuth(),
    title=f"{settings.SITE_INFO['site_name']} API",
    version="1.0.0",
    description=f"{settings.SITE_INFO['site_name']} API <hr/><a href='{settings.SITE_INFO['site_url']}'>Learn more</a>",
)

NOT_FOUND = 404, {"message": "Not found"}
OK = 200, {"message": "OK"}
NO_DATA = {"data": [], "count": 0, "pages": 0}
INVALID_PAGE = 400, {"message": "Invalid page number"}
