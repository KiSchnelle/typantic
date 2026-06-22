"""Shared type-introspection helpers for typantic.

Small pure helpers for unwrapping annotations, used by both the decorator
(:mod:`typantic._decorator`) and the config-file support
(:mod:`typantic._config_file`); kept here to avoid an import cycle between them.
"""

import types
from typing import (
    Annotated,
    TypeGuard,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel


def extract_base_type(annotation: object) -> object:
    """Strip ``Annotated`` validator metadata, keeping the structural type.

    Recursively walks through ``Annotated``, ``Union``, ``list``, and
    ``tuple`` wrappers, discarding everything except the base types that
    Typer can interpret. ``Literal`` annotations are passed through
    untouched -- Typer renders them as CLI choices.

    Args:
        annotation: A (possibly nested) type annotation to unwrap.

    Returns:
        The base type with all Pydantic validator metadata removed.

    Examples:
        >>> from typing import Annotated
        >>> from pydantic import AfterValidator, Field
        >>> extract_base_type(Annotated[float, Field(description="x")])
        <class 'float'>
    """
    if get_origin(annotation) is Annotated:
        inner = get_args(annotation)[0]
        return extract_base_type(inner)

    if get_origin(annotation) in (Union, types.UnionType):
        cleaned = tuple(extract_base_type(a) for a in get_args(annotation))
        return Union[cleaned]  # noqa: UP007

    if get_origin(annotation) is list:
        args = get_args(annotation)
        if args:
            return list[extract_base_type(args[0])]  # type: ignore[misc]

    if get_origin(annotation) is tuple:
        args = get_args(annotation)
        if args:
            cleaned = tuple(extract_base_type(a) for a in args)
            return tuple[cleaned]  # type: ignore[valid-type]

    return annotation


def is_model_type(tp: object) -> TypeGuard[type[BaseModel]]:
    """Return ``True`` if ``tp`` is a concrete ``BaseModel`` subclass."""
    return isinstance(tp, type) and issubclass(tp, BaseModel) and tp is not BaseModel
