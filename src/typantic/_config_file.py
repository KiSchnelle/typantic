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
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from typantic._introspect import (
    extract_base_type,
    field_input_key,
    is_model_type,
    model_hints,
)

_YAML_SUFFIXES = {".yaml", ".yml"}
_SUFFIXES = _YAML_SUFFIXES | {".json"}

# Placeholder for a field whose default is produced by a ``default_factory``.
# Such values are host/time-sensitive (a timestamped path, a CPU count) and must
# not be frozen into a shared template, so the template shows this sentinel and
# ``load_config_file`` strips it -- letting the factory run fresh on reload.
_DEFAULT_SENTINEL = "<DEFAULT: computed at runtime>"


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
    return cast("dict[str, Any]", _strip_default_sentinels(data))


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
    """Config-file keys matching no field on ``model_cls`` (recursing into models).

    A field is accepted under its input key (its alias, where the model needs
    one -- which is what a generated template and the web form both write) as well
    as its own name. Computed-field names are allowed so a written run-config
    (which serialises them) still round-trips on reload; anything else is almost
    certainly a typo that Pydantic's ``extra="ignore"`` would otherwise drop in
    silence. Only dict values are recursed into, and only for fields whose bare
    annotation is a concrete model -- optionals and lists of models are left alone
    rather than risk a false rejection.
    """
    hints = model_hints(model_cls)
    by_key = {
        field_input_key(model_cls, name, field): name
        for name, field in model_cls.model_fields.items()
    }
    allowed = set(by_key) | set(model_cls.model_fields)
    allowed |= set(model_cls.model_computed_fields)
    unknown: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if key not in allowed:
            unknown.append(dotted)
            continue
        name = by_key.get(key, key)
        if name in model_cls.model_fields and isinstance(value, dict):
            nested = extract_base_type(hints[name])
            if is_model_type(nested):
                unknown.extend(
                    unknown_config_keys(nested, value, prefix=f"{dotted}."),
                )
    return unknown


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
    placeholder = f"<REQUIRED: {field.description or name}>"
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
        key = field_input_key(model_cls, name, field)
        if field.is_required():
            base_type = extract_base_type(hints[name])
            template[key] = _required_placeholder(name, field, base_type, seen)
        elif field.default_factory is not None:
            template[key] = _DEFAULT_SENTINEL
        else:
            template[key] = to_jsonable_python(field.default)
    return template


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
