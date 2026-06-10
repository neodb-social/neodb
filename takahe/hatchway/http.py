import json
from typing import Generic, TypeVar

from django.core.serializers.json import DjangoJSONEncoder
from django.http.response import HttpResponseBase
from django.utils.functional import cached_property

T = TypeVar("T")


class ApiResponse(Generic[T], HttpResponseBase):
    """
    A way to return extra information with a response if you want
    headers, etc.
    """

    streaming = False

    def __init__(
        self,
        data: T,
        encoder=DjangoJSONEncoder,
        json_dumps_params: dict[str, object] | None = None,
        **kwargs,
    ):
        self.data = data
        self.encoder = encoder
        self.json_dumps_params = json_dumps_params or {}
        kwargs.setdefault("content_type", "application/json")
        super().__init__(**kwargs)

    @property
    def content(self):
        return self.text.encode("utf-8")

    @cached_property
    def text(self):
        return json.dumps(
            self.data,
            cls=self.encoder,
            **self.json_dumps_params,
        )

    def __iter__(self):
        yield self.content


class ApiError(BaseException):
    """
    A handy way to raise an error with JSONable contents
    """

    def __init__(self, status: int, error: str):
        self.status = status
        self.error = error
