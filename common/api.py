from ninja import NinjaAPI, Schema
from django.conf import settings
from typing import Any, Callable, List, Optional, Tuple, Type
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination
from django.db.models import QuerySet
from ninja.security import HttpBearer
from oauthlib.oauth2 import Server
from oauth2_provider.oauth2_backends import OAuthLibCore
from oauth2_provider.oauth2_validators import OAuth2Validator
import logging

_logger = logging.getLogger(__name__)


class OAuthAccessTokenAuth(HttpBearer):
    def authenticate(self, request, token):
        if not token or not request.user.is_authenticated:
            _logger.debug("API auth: no access token or user not authenticated")
            return False
        request_scopes = []
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            request_scopes = ["read"]
        else:
            request_scopes = ["write"]
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
    title=settings.SITE_INFO["site_name"] + " API",
    version="1.0.0",
    description=f"{settings.SITE_INFO['site_name']} API <hr/><a href='{settings.APP_WEBSITE}'>Learn more</a>",
)
