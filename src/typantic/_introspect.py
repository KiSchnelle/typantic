"""Shared type-introspection helpers for typantic.

Small pure helpers for unwrapping annotations, used by both the decorator
(:mod:`typantic._decorator`) and the config-file support
(:mod:`typantic._config_file`); kept here to avoid an import cycle between them.
"""

import collections
import collections.abc
import types
from typing import (
    Annotated,
    TypeAliasType,
    TypeGuard,
    Union,
    cast,
    get_args,
    get_origin,
)

from pydantic import AliasChoices, BaseModel
from pydantic.fields import FieldInfo

# Collections the CLI gathers as a repeated flag (a list); Pydantic coerces the
# list back into the declared collection.
_SEQUENCES = (
    list,
    set,
    frozenset,
    collections.deque,
    collections.abc.Sequence,
    collections.abc.MutableSequence,
    collections.abc.Set,
    collections.abc.MutableSet,
)


def extract_base_type(annotation: object) -> object:
    """Strip ``Annotated`` validator metadata, keeping the structural type.

    Recursively walks through ``Annotated``, ``Union``, ``list``, ``set`` and
    ``tuple`` wrappers, discarding everything except the base types that
    Typer can interpret. ``Literal`` annotations are passed through
    untouched -- Typer renders them as CLI choices.

    Typer renders only ``list`` among the collections, so a ``set`` /
    ``frozenset`` / ``Sequence`` / ``deque`` / variadic ``tuple[X, ...]`` is mapped
    to ``list[X]``: the CLI gathers repeated values into a list and Pydantic
    coerces it back to the declared type, which it does natively. A *fixed* tuple
    (``tuple[int, int]``) keeps its shape -- Typer renders it as a multi-value
    option. A ``NewType`` or a PEP 695 ``type`` alias is replaced by what it wraps.

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
    annotation = unwrap(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (Union, types.UnionType):
        cleaned = tuple(extract_base_type(a) for a in args)
        return Union[cleaned]  # noqa: UP007

    repeated = origin in _SEQUENCES or (origin is tuple and _is_variadic_tuple(args))
    if repeated and args:
        return list[extract_base_type(args[0])]  # type: ignore[misc]

    if origin is tuple and args:
        cleaned = tuple(extract_base_type(a) for a in args)
        return tuple[cleaned]  # type: ignore[valid-type]

    return annotation


def unwrap(annotation: object) -> object:
    """Peel ``Annotated``, PEP 695 ``type`` aliases and ``NewType`` s off a type."""
    while True:
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        elif isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        elif getattr(annotation, "__supertype__", None) is not None:
            annotation = annotation.__supertype__  # type: ignore[attr-defined]
        else:
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


def _by_name(model_cls: type[BaseModel]) -> bool:
    """Whether the model accepts a field under its own name despite an alias."""
    config = model_cls.model_config
    # validate_by_name is the 2.11+ spelling; populate_by_name still works.
    return bool(config.get("populate_by_name") or config.get("validate_by_name"))


def _alias_paths(field: FieldInfo) -> list[tuple[str | int, ...]]:
    """The field's validation alias as key paths, in Pydantic's precedence order.

    ``validation_alias`` alone is enough: Pydantic mirrors a plain ``alias`` (and
    an ``alias_generator``'s) into it, so it is set whenever an alias applies. A
    plain string is a one-key path, an ``AliasPath`` a nested one, and an
    ``AliasChoices`` contributes each of its choices in turn.
    """
    alias = field.validation_alias
    if alias is None:
        return []
    choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
    return [
        (choice,) if isinstance(choice, str) else tuple(choice.path)
        for choice in choices
    ]


def _key_paths(
    model_cls: type[BaseModel],
    name: str,
    field: FieldInfo,
) -> list[tuple[str | int, ...]]:
    """Every key path under which ``model_cls`` accepts ``name``, in precedence order.

    An alias is honoured unless the model turned ``validate_by_alias`` off; the
    field's own name is accepted when there is no alias, or when the model opts
    into ``populate_by_name`` / ``validate_by_name``. Pydantic tries the aliases
    first, so they come first.
    """
    aliases = _alias_paths(field)
    paths: list[tuple[str | int, ...]] = []
    if aliases and model_cls.model_config.get("validate_by_alias", True):
        paths.extend(aliases)
    if not aliases or _by_name(model_cls):
        paths.append((name,))
    return paths


def accepted_keys(model_cls: type[BaseModel], name: str, field: FieldInfo) -> list[str]:
    """The top-level mapping keys under which ``model_cls`` accepts field ``name``.

    Pydantic's precedence order: aliases first, then the field's own name where
    the model accepts it. An ``AliasPath`` contributes its first key -- the value
    under it is the path's nested structure, not a flat value.
    """
    keys = [path[0] for path in _key_paths(model_cls, name, field)]
    return list(dict.fromkeys(key for key in keys if isinstance(key, str)))


def flat_keys(model_cls: type[BaseModel], name: str, field: FieldInfo) -> list[str]:
    """The single-key spellings of field ``name`` (no ``AliasPath``), by precedence."""
    return [
        path[0]
        for path in _key_paths(model_cls, name, field)
        if len(path) == 1 and isinstance(path[0], str)
    ]


def canonical_key_path(
    model_cls: type[BaseModel],
    name: str,
    field: FieldInfo,
) -> tuple[str, ...]:
    """The one key path typantic writes field ``name`` under.

    A generated template writes the field there, a passed flag is re-nested
    there, and a config file's other spellings are moved onto it -- so a flag
    always overrides the file, whichever spelling the file used. The field's
    name wins when the model accepts it (it matches the flag the user types);
    otherwise the first alias Pydantic would accept. An ``AliasPath`` is a nested
    path, usable only while every step is a key rather than a list index.

    Raises:
        ValueError: If the only spellings Pydantic accepts are ``AliasPath`` s
            through a list index, which typantic cannot write a value into.
    """
    paths = _key_paths(model_cls, name, field)
    if (name,) in paths:
        return (name,)
    for path in paths:
        if all(isinstance(step, str) for step in path):
            return cast("tuple[str, ...]", path)
    msg = (
        f"Field {name!r} can only be set through an AliasPath into a list, which "
        f"typantic cannot write a value into. Set "
        f"model_config['populate_by_name'] = True, or add a string alias."
    )
    raise ValueError(msg)


def field_input_key(model_cls: type[BaseModel], name: str, field: FieldInfo) -> str:
    """The mapping key a CLI flag's value is re-nested under for field ``name``.

    Pydantic populates by *alias*, not by field name, unless the model opts into
    ``populate_by_name``. Passing the field name to an aliased model would land in
    ``extra`` and be dropped in silence, so the value must be re-keyed onto an
    accepted key before the model is built (see :func:`canonical_key_path`). The
    flag the user types is unaffected -- it always follows the field name.

    Args:
        model_cls: The model the value will be passed to.
        name: The field name.
        field: The field's metadata.

    Returns:
        The field's canonical key.

    Raises:
        ValueError: If every spelling Pydantic accepts is a nested ``AliasPath``,
            which a single CLI parameter cannot be re-nested under.
    """
    path = canonical_key_path(model_cls, name, field)
    if len(path) != 1:
        msg = (
            f"Field {name!r} can only be set through an AliasPath, which typantic "
            f"cannot map onto a single CLI parameter. Set "
            f"model_config['populate_by_name'] = True, add a string alias, or use "
            f'config_file="only".'
        )
        raise ValueError(msg)
    return path[0]


def nested_model(annotation: object) -> type[BaseModel] | None:
    """The model a field holds -- directly, or as the one member of ``X | None``."""
    base = extract_base_type(annotation)
    if is_model_type(base):
        return base
    if get_origin(base) in (Union, types.UnionType):
        members = [arg for arg in get_args(base) if arg is not type(None)]
        if len(members) == 1 and is_model_type(members[0]):
            return members[0]
    return None


def item_model(annotation: object) -> type[BaseModel] | None:
    """The model a list/set/tuple field holds per item (``X | None`` unwrapped)."""
    base = extract_base_type(annotation)
    if get_origin(base) in (Union, types.UnionType):
        members = [arg for arg in get_args(base) if arg is not type(None)]
        if len(members) != 1:
            return None
        base = members[0]
    args = get_args(base)
    if get_origin(base) is list and len(args) == 1 and is_model_type(args[0]):
        return args[0]
    return None
