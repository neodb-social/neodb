import typing
from types import NoneType, UnionType


def typeinfo(t) -> tuple[typing.Type, bool, dict]:
    """
    Returns a tuple of (actual_type, optional, annotations) for the given outer type.
    """
    origin = typing.get_origin(t)
    args = typing.get_args(t)
    if origin is typing.Annotated:
        inner_type, optional, annotations = typeinfo(args[0])
        for data in args[1:]:
            if isinstance(data, dict):
                annotations.update(data)
        return inner_type, optional, annotations
    elif origin in (typing.Union, UnionType) and NoneType in args:
        actual_types = [a for a in args if a is not NoneType]
        if len(actual_types) == 1:
            return actual_types[0], True, {}
        else:
            raise ValueError(f"Optional union type `{t}` not currently supported.")
    return t, False, {}
