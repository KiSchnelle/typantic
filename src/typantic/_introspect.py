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
from pydantic.fields import FieldInfo


def extract_base_type(annotation: object) -> object:
    """Strip ``Annotated`` validator metadata, keeping the structural type.

    Recursively walks through ``Annotated``, ``Union``, ``list``, ``set`` and
    ``tuple`` wrappers, discarding everything except the base types that
    Typer can interpret. ``Literal`` annotations are passed through
    untouched -- Typer renders them as CLI choices.

    Typer renders only ``list`` among the collections, so a ``set`` /
    ``frozenset`` / variadic ``tuple[X, ...]`` is mapped to ``list[X]``: the CLI
    gathers repeated values into a list and Pydantic coerces it back to the
    declared type, which it does natively. A *fixed* tuple (``tuple[int, int]``)
    keeps its shape -- Typer renders it as a multi-value option.

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

    if get_origin(annotation) in (list, set, frozenset):
        args = get_args(annotation)
        if args:
            return list[extract_base_type(args[0])]  # type: ignore[misc]

    if get_origin(annotation) is tuple:
        args = get_args(annotation)
        if _is_variadic_tuple(args):
            return list[extract_base_type(args[0])]  # type: ignore[misc]
        if args:
            cleaned = tuple(extract_base_type(a) for a in args)
            return tuple[cleaned]  # type: ignore[valid-type]

    return annotation


def _is_variadic_tuple(args: tuple[object, ...]) -> bool:
    """Whether ``args`` came from a ``tuple[X, ...]`` (unbounded) annotation."""
    return len(args) == 2 and args[1] is Ellipsis  # noqa: PLR2004 - (item, ...)


def is_model_type(tp: object) -> TypeGuard[type[BaseModel]]:
    """Return ``True`` if ``tp`` is a concrete ``BaseModel`` subclass."""
    return isinstance(tp, type) and issubclass(tp, BaseModel) and tp is not BaseModel


def model_hints(model_cls: type[BaseModel]) -> dict[str, object]:
    """Each field's annotation, as Pydantic already resolved it.

    Pydantic resolves a model's annotations when the class is built, capturing the
    namespace it was defined in. Re-resolving them with ``get_type_hints`` only
    sees the *module* globals, so a model defined in a local scope under
    ``from __future__ import annotations`` (where the annotation is a string)
    raises ``NameError`` for a class Pydantic itself handles fine. Reading the
    already-resolved annotation off each field sidesteps that entirely.

    The ``Annotated`` metadata is not carried here -- Pydantic moves it onto
    ``FieldInfo.metadata``, which is where the constraint helpers read it from,
    and :func:`extract_base_type` discards it anyway.
    """
    return {name: field.annotation for name, field in model_cls.model_fields.items()}


def field_input_key(model_cls: type[BaseModel], name: str, field: FieldInfo) -> str:
    """The mapping key ``model_cls(**{key: value})`` accepts for field ``name``.

    Pydantic populates by *alias*, not by field name, unless the model opts into
    ``populate_by_name``. Passing the field name to an aliased model would land in
    ``extra`` and be dropped in silence, so the CLI value must be re-keyed onto
    the alias before the model is built. The flag the user types is unaffected --
    it always follows the field name.

    Args:
        model_cls: The model the value will be passed to.
        name: The field name.
        field: The field's metadata.

    Returns:
        The alias when one is needed and expressible, else the field name.

    Raises:
        ValueError: If the field's validation alias is an ``AliasChoices`` /
            ``AliasPath``, which cannot be expressed as a single keyword.
    """
    config = model_cls.model_config
    # validate_by_name is the 2.11+ spelling; populate_by_name still works.
    if config.get("populate_by_name") or config.get("validate_by_name"):
        return name

    # validation_alias alone is enough: Pydantic mirrors a plain `alias` (and an
    # alias_generator's) into it, so it is set whenever an alias applies at all.
    alias = field.validation_alias
    if alias is None:
        return name
    if not isinstance(alias, str):
        msg = (
            f"Field {name!r} uses a {type(alias).__name__} validation alias, which "
            f"typantic cannot map onto a single CLI parameter. Set "
            f"model_config['populate_by_name'] = True, or use a plain string alias."
        )
        # ValueError, not TypeError: this is a model that cannot map onto a CLI,
        # the same class of error (and exception type) as a name collision.
        raise ValueError(msg)  # noqa: TRY004
    return alias
