import logging
from typing import Any, Callable, List, Optional, Tuple, Type

from django.conf import settings
from django.db.models import QuerySet
from ninja import NinjaAPI, Schema
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination
from ninja.security import HttpBearer
from oauth2_provider.oauth2_backends import OAuthLibCore
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauthlib.oauth2 import Server

_logger = logging.getLogger(__name__)


PERMITTED_WRITE_METHODS = ["PUT", "POST", "DELETE", "PATCH"]
PERMITTED_READ_METHODS = ["GET", "HEAD", "OPTIONS"]

class OAuthAccessTokenAuth(HttpBearer):
    def authenticate(self, request, token) -> bool:
        if not token or not request.user.is_authenticated:
            _logger.debug("API auth: no access token or user not authenticated")
            return False
        request_scopes = []
        request_method = request_method.upper()
        if request_method in PERMITTED_READ_METHODS:
            request_scopes = ["read"]
        elif request_method in PERMITTED_WRITE_METHODS:
            request_scopes = ["write"]
        else:
            return False
        validator = OAuth2Validator()
        core = OAuthLibCore(Server(validator))
        valid, oauthlib_req = core.verify_request(request, scopes=request_scopes)
        if not valid:
            _logger.debug(f"API auth: request scope {request_scopes} not verified")
        return valid


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
    title=f'{settings.SITE_INFO["site_name"]} API',
    version="1.0.0",
    description=f"{settings.SITE_INFO['site_name']} API <hr/><a href='{settings.SITE_INFO['site_url']}'>Learn more</a>",
)
