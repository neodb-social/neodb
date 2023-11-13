import logging
from typing import Any, Callable, List, Optional, Tuple, Type

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from ninja import NinjaAPI, Schema
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination
from ninja.security import HttpBearer

from takahe.utils import Takahe

_logger = logging.getLogger(__name__)


PERMITTED_WRITE_METHODS = ["PUT", "POST", "DELETE", "PATCH"]
PERMITTED_READ_METHODS = ["GET", "HEAD", "OPTIONS"]


class OAuthAccessTokenAuth(HttpBearer):
    def authenticate(self, request, token) -> bool:
        if not token:
            _logger.debug("API auth: no access token")
            request.user = AnonymousUser()
        else:
            request.user = Takahe.get_local_user_by_token(token) or AnonymousUser()
        return request.user and request.user.is_authenticated


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
