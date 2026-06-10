from typing import Literal, Optional, Union

from django.core.files import File

from hatchway.http import ApiResponse
from hatchway.types import (
    BaseModel,
    Query,
    QueryType,
    acceptable_input,
    extract_output_type,
    extract_signifier,
    is_model_subclass,
    is_optional,
)


def test_is_optional():
    assert is_optional(Optional[int]) == (True, int)
    assert is_optional(Union[int, None]) == (True, int)
    assert is_optional(Union[None, int]) == (True, int)
    assert is_optional(int | None) == (True, int)
    assert is_optional(None | int) == (True, int)
    assert is_optional(int) == (False, int)
    assert is_optional(Query[int]) == (False, Query[int])


def test_extract_signifier():
    assert extract_signifier(int) == (None, int)
    assert extract_signifier(Query[int]) == (QueryType, int)
    assert extract_signifier(Query[Optional[int]]) == (  # type:ignore
        QueryType,
        Optional[int],
    )
    assert extract_signifier(Query[int | None]) == (  # type:ignore
        QueryType,
        Optional[int],
    )
    assert extract_signifier(Optional[Query[int]]) == (QueryType, Optional[int])


def test_extract_output_type():
    assert extract_output_type(int) is int
    assert extract_output_type(ApiResponse[int]) is int
    assert extract_output_type(ApiResponse[int | str]) == int | str


def test_acceptable_input():
    assert acceptable_input(str) is True
    assert acceptable_input(int) is True
    assert acceptable_input(Query[int]) is True
    assert acceptable_input(Optional[int]) is True
    assert acceptable_input(int | None) is True
    assert acceptable_input(int | str | None) is True
    assert acceptable_input(Query[int | None]) is True  # type: ignore
    assert acceptable_input(File) is True
    assert acceptable_input(list[str]) is True
    assert acceptable_input(dict[str, int]) is True
    assert acceptable_input(Literal["a", "b"]) is True
    assert acceptable_input(frozenset) is True
    assert acceptable_input(dict[str, frozenset]) is True
    assert acceptable_input(memoryview) is False
    assert acceptable_input(dict[str, memoryview]) is False


def test_is_model_subclass():
    class ModelSubclass(BaseModel):
        pass

    assert is_model_subclass(str) is False
    assert is_model_subclass(type) is False
    assert is_model_subclass(BaseModel) is True
    assert is_model_subclass(ModelSubclass) is True
