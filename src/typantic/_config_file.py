"""Reading settings from, and writing templates to, YAML / JSON config files.

Backs typantic's opt-in ``config_file=True`` CLI behaviour (a ``--config`` to load
settings from a file and a ``--generate-config`` to emit an editable default
template), and is usable directly.

:func:`build_config_template` produces a default-value mapping straight from a
model's fields -- required fields become ``<REQUIRED: ...>`` placeholders (nested
models recurse and required lists become a single-element example list, so their
structure is shown and the shape reloads validly), factory-defaulted fields
become a ``<DEFAULT: ...>`` sentinel that :func:`load_config_file` strips (so a
host/time-sensitive default is recomputed fresh rather than frozen into a shared
template), and every other field is serialised the way pydantic would in JSON
mode, so nested models, sets, datetimes, paths and enums all round-trip.
"""

import json
from pathlib import Path
from typing import Any, cast, get_args, get_origin

import yaml
from pydantic import BaseModel, SecretBytes, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from typantic._introspect import (
    accepted_keys,
    canonical_key_path,
    extract_base_type,
    flat_keys,
    is_model_type,
    item_model,
    model_hints,
    nested_model,
)

_YAML_SUFFIXES = {".yaml", ".yml"}
_SUFFIXES = _YAML_SUFFIXES | {".json"}

# Placeholder for a field whose default is produced by a ``default_factory``.
# Such values are host/time-sensitive (a timestamped path, a CPU count) and must
# not be frozen into a shared template, so the template shows this sentinel and
# ``load_config_file`` strips it -- letting the factory run fresh on reload.
_DEFAULT_SENTINEL = "<DEFAULT: computed at runtime>"

# How a required field's placeholder starts; a loaded file still holding one was
# never filled in.
_REQUIRED_PREFIX = "<REQUIRED:"


def load_config_file(path: Path) -> dict[str, Any]:
    """Read a settings mapping from a ``.yaml`` / ``.yml`` / ``.json`` file.

    Args:
        path: The config file to read.

    Returns:
        The parsed top-level mapping of settings.

    Raises:
        ValueError: For an unsupported suffix, a document that cannot be parsed,
            or a document whose top level is not a mapping.

    """
    suffix = path.suffix.lower()
    if suffix not in _SUFFIXES:
        msg = f"Unsupported config file type '{suffix}'; use .yaml or .json."
        raise ValueError(msg)

    text = path.read_text()
    try:
        data = yaml.safe_load(text) if suffix in _YAML_SUFFIXES else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        msg = f"Config file {path} could not be parsed: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        # ValueError (not TypeError) keeps one exception type for any bad config.
        msg = f"Config file {path} must contain a mapping of settings."
        raise ValueError(msg)  # noqa: TRY004
    unfilled = _placeholders(data)
    if unfilled:
        # An unedited template would otherwise run with the placeholder text as
        # the value -- a path literally named "<REQUIRED: Output directory.>".
        msg = f"Config file {path} still needs values for: {', '.join(unfilled)}"
        raise ValueError(msg)
    return cast("dict[str, Any]", _strip_default_sentinels(data))


def _placeholders(data: object, prefix: str = "") -> list[str]:
    """The dotted paths in ``data`` still holding a ``<REQUIRED: ...>`` placeholder."""
    if isinstance(data, dict):
        return [
            found
            for key, value in cast("dict[str, Any]", data).items()
            for found in _placeholders(value, f"{prefix}{key}.")
        ]
    if isinstance(data, list):
        return [
            found
            for index, item in enumerate(cast("list[object]", data))
            for found in _placeholders(item, f"{prefix.removesuffix('.')}[{index}].")
        ]
    if isinstance(data, str) and data.startswith(_REQUIRED_PREFIX):
        return [prefix.removesuffix(".")]
    return []


def _strip_default_sentinels(data: object) -> object:
    """Recursively drop mapping keys whose value is the default sentinel.

    A ``<DEFAULT: ...>`` value marks a field whose default is factory-computed and
    was left unedited in the template; removing the key lets the model's
    ``default_factory`` run fresh on load instead of replaying a stale value.
    """
    if isinstance(data, dict):
        return {
            key: _strip_default_sentinels(value)
            for key, value in cast("dict[str, Any]", data).items()
            if value != _DEFAULT_SENTINEL
        }
    if isinstance(data, list):
        return [_strip_default_sentinels(item) for item in data]
    return data


def unknown_config_keys(
    model_cls: type[BaseModel],
    data: dict[str, Any],
    prefix: str = "",
) -> list[str]:
    """Config-file keys ``model_cls`` would not accept (recursing into models).

    A field is accepted under exactly the keys Pydantic accepts for it (see
    :func:`typantic._introspect.accepted_keys`) -- its alias, and its own name
    only where the model allows that. Computed-field names are allowed too, so a
    written run-config (which serialises them) still round-trips. Anything else is
    almost certainly a typo Pydantic would drop in silence; a field's own name on
    a model that accepts only its alias is reported with the key to use instead.
    Nested models are checked inside a model value, an ``X | None`` value, and
    each item of a list/set/tuple of models (reported as ``mounts[1].dest``). A
    model that allows extra keys (``extra="allow"``) is not checked at all.
    """
    if model_cls.model_config.get("extra") == "allow":
        return []
    hints = model_hints(model_cls)
    owner: dict[str, str] = {}
    for name, field in model_cls.model_fields.items():
        owner.update(dict.fromkeys(accepted_keys(model_cls, name, field), name))
    computed = set(model_cls.model_computed_fields)
    unknown: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        field_name = owner.get(key)
        if field_name is None:
            if key not in computed:
                unknown.append(dotted + _spelling_hint(model_cls, key))
            continue
        unknown.extend(_unknown_below(hints[field_name], value, dotted))
    return unknown


def _spelling_hint(model_cls: type[BaseModel], key: str) -> str:
    """A ``(use 'thr')`` hint for a field's name the model accepts only aliased."""
    field = model_cls.model_fields.get(key)
    if field is None:
        return ""
    keys = accepted_keys(model_cls, key, field)
    return f" (use {keys[0]!r})" if keys else ""


def _unknown_below(annotation: object, value: object, dotted: str) -> list[str]:
    """Unknown keys inside a field's value, for fields that hold models."""
    model = nested_model(annotation)
    if model is not None and isinstance(value, dict):
        return unknown_config_keys(model, cast("dict[str, Any]", value), f"{dotted}.")
    model = item_model(annotation)
    if model is not None and isinstance(value, list):
        unknown: list[str] = []
        for index, item in enumerate(cast("list[object]", value)):
            if isinstance(item, dict):
                unknown.extend(
                    unknown_config_keys(
                        model,
                        cast("dict[str, Any]", item),
                        f"{dotted}[{index}].",
                    ),
                )
        return unknown
    return []


def canonicalize(model_cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with every field under its canonical key, computed fields dropped.

    A config file may spell a field any way Pydantic accepts (an alias, a choice,
    or the field's name), but a passed flag is re-nested under the canonical key
    (:func:`typantic._introspect.canonical_key_path`). Moving the file's spelling
    onto that key first is what lets the flag override the file -- otherwise both
    keys reach Pydantic and its alias-first precedence lets the file win. Where
    the file spells a field twice, the spelling Pydantic would have used wins.

    Written-back computed fields are dropped: Pydantic recomputes them, and a
    model with ``extra="forbid"`` would reject them. Models nested in a model
    value, an ``X | None`` value, or a list of models are canonicalised too.
    """
    out = {
        key: value
        for key, value in data.items()
        if key not in model_cls.model_computed_fields
    }
    hints = model_hints(model_cls)
    for name, field in model_cls.model_fields.items():
        path = canonical_key_path(model_cls, name, field)
        if len(path) != 1:
            continue  # an AliasPath: the file's nested structure is Pydantic's
        canonical = path[0]
        present = [key for key in flat_keys(model_cls, name, field) if key in out]
        if present:
            chosen = out[present[0]]
            for key in present:
                del out[key]
            out[canonical] = chosen
        if canonical in out:
            out[canonical] = _canonical_below(hints[name], out[canonical])
    return out


def _canonical_below(annotation: object, value: object) -> object:
    """Canonicalise the models inside a field's value."""
    model = nested_model(annotation)
    if model is not None and isinstance(value, dict):
        return canonicalize(model, cast("dict[str, Any]", value))
    model = item_model(annotation)
    if model is not None and isinstance(value, list):
        return [
            canonicalize(model, cast("dict[str, Any]", item))
            if isinstance(item, dict)
            else item
            for item in cast("list[object]", value)
        ]
    return value


def _required_placeholder(
    name: str,
    field: FieldInfo,
    base_type: object,
    seen: frozenset[type[BaseModel]],
) -> object:
    """Build the template entry for a required field.

    Nested models recurse into their own template and lists into a single-element
    example list (a model template for lists of models, else the scalar
    placeholder) so the shown shape reloads as a list; anything else becomes a
    ``<REQUIRED: ...>`` string. ``seen`` breaks self-referential models, which
    would otherwise recurse until the stack ran out.
    """
    placeholder = f"{_REQUIRED_PREFIX} {field.description or name}>"
    if is_model_type(base_type):
        if base_type in seen:
            return placeholder
        return build_config_template(base_type, _seen=seen)
    if get_origin(base_type) is list:
        args = get_args(base_type)
        if args and is_model_type(args[0]):
            if args[0] in seen:
                return [placeholder]
            return [build_config_template(args[0], _seen=seen)]
        return [placeholder]
    return placeholder


def build_config_template(
    model_cls: type[BaseModel],
    _seen: frozenset[type[BaseModel]] = frozenset(),
) -> dict[str, object]:
    """Build an editable default-config mapping for a settings model.

    Required fields (no default) become ``<REQUIRED: ...>`` placeholders (nested
    models recurse; required lists become a single-element example list);
    factory-defaulted fields become a ``<DEFAULT: ...>`` sentinel (their value is
    host/time-sensitive, so it is left for the factory to compute fresh on load);
    every other field gets its static default, serialised as pydantic would in
    JSON mode. Integer defaults render in decimal, so an octal mode such as
    ``0o775`` appears as ``509``.

    Each entry is keyed by the field's *input key* -- its alias, where the model
    needs one -- so the written file is one the model can actually load back.

    Args:
        model_cls: The settings model to template.
        _seen: Models already being templated, to break self-referential cycles.

    Returns:
        A JSON/YAML-serialisable mapping of input key to default or placeholder.
    """
    hints = model_hints(model_cls)
    seen = _seen | {model_cls}
    template: dict[str, object] = {}
    for name, field in model_cls.model_fields.items():
        value: object
        if field.is_required():
            base_type = extract_base_type(hints[name])
            value = _required_placeholder(name, field, base_type, seen)
        elif field.default_factory is not None or _holds_secret(field.default):
            # A secret would be written as its mask and read back as the value;
            # the sentinel is stripped on load, so the real default applies.
            value = _DEFAULT_SENTINEL
        else:
            value = _template_value(field.default)
        _put(template, canonical_key_path(model_cls, name, field), value)
    return template


def _holds_secret(value: object) -> bool:
    """Whether ``value`` is a secret, or a model holding one at any depth."""
    if isinstance(value, SecretStr | SecretBytes):
        return True
    if isinstance(value, BaseModel):
        return any(
            _holds_secret(getattr(value, name)) for name in type(value).model_fields
        )
    return False


def _template_value(value: object) -> object:
    """``value`` as a template entry that loads back into the same value.

    A model instance (at any depth, including inside containers) is written field
    by field under each field's canonical key -- the key a load accepts -- rather
    than dumped by serialization alias, which a load rejects. Everything else is
    serialised the way Pydantic would in JSON mode.
    """
    if isinstance(value, BaseModel):
        model_cls = type(value)
        entry: dict[str, object] = {}
        for name, field in model_cls.model_fields.items():
            _put(
                entry,
                canonical_key_path(model_cls, name, field),
                _template_value(getattr(value, name)),
            )
        return entry
    if isinstance(value, list | tuple | set | frozenset):
        return [_template_value(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        return {
            to_jsonable_python(key): _template_value(item)
            for key, item in cast("dict[object, object]", value).items()
        }
    return to_jsonable_python(value)


def _put(template: dict[str, object], path: tuple[str, ...], value: object) -> None:
    """Write ``value`` at the nested key ``path`` (an ``AliasPath`` nests)."""
    target = template
    for key in path[:-1]:
        target = cast("dict[str, object]", target.setdefault(key, {}))
    target[path[-1]] = value


def write_config_template(model_cls: type[BaseModel], path: Path) -> None:
    """Write an editable default config template for a settings model.

    The format follows the path suffix: ``.json`` is written as JSON, anything
    else as YAML. See :func:`build_config_template` for the templating rules.

    Args:
        model_cls: The settings model to template.
        path: Destination file (``.json`` for JSON, otherwise YAML).
    """
    template = build_config_template(model_cls)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(template, indent=2))
    else:
        path.write_text(yaml.safe_dump(template, sort_keys=False))
