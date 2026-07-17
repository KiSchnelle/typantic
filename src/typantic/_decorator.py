"""Core decorator for converting Pydantic models to Typer CLI interfaces."""

import inspect
import json
import types
from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    NamedTuple,
    Union,
    cast,
    get_args,
    get_origin,
)

import annotated_types
import typer
from pydantic import BaseModel, SecretBytes, SecretStr, ValidationError
from pydantic.fields import FieldInfo

from typantic._config_file import (
    load_config_file,
    unknown_config_keys,
    write_config_template,
)
from typantic._introspect import extract_base_type as _extract_base_type
from typantic._introspect import field_input_key as _field_input_key
from typantic._introspect import is_model_type as _is_model_type
from typantic._introspect import model_hints as _model_hints


class _Missing:
    """Sentinel type for "no value supplied" (distinct from a ``None`` default)."""


_MISSING = _Missing()


class _Leaf(NamedTuple):
    """One flattened CLI parameter and the names it is known by.

    The flag the user types follows the field *names* (``name_path``), while the
    value is re-nested under the field's *input keys* (``key_path``) -- its
    aliases, where the model needs them. The two differ only for aliased models.
    """

    cli_name: str
    key_path: tuple[str, ...]
    name_path: tuple[str, ...]
    flags: tuple[str, ...]

# Python identifiers for the injected config-file parameters (kept distinct from
# any model field name); the user-facing flags are --config / --generate-config.
_CTX_PARAM = "_typantic_ctx"
_CONFIG_PARAM = "_typantic_config"
_GENERATE_PARAM = "_typantic_generate_config"
_SCHEMA_PARAM = "_typantic_schema"
_CONFIG_PANEL = "Config file"
_EXPLICIT_SOURCES = frozenset({"COMMANDLINE", "ENVIRONMENT", "PROMPT"})


def _value_is_explicit(ctx: typer.Context, name: str) -> bool:
    """Whether a CLI parameter's value came from the user, not its default."""
    source = ctx.get_parameter_source(name)
    return source is not None and source.name in _EXPLICIT_SOURCES


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    """Assign ``value`` at the nested ``path`` in ``data``, creating sub-dicts."""
    target = data
    for part in path[:-1]:
        existing = target.get(part)
        if not isinstance(existing, dict):
            existing = {}
            target[part] = existing
        target = cast("dict[str, Any]", existing)
    target[path[-1]] = value


def _construct(model_cls: type[BaseModel], data: dict[str, Any]) -> BaseModel:
    """Build the model from ``data``, reporting errors as Typer parameter errors."""
    try:
        return model_cls(**data)
    except ValidationError as exc:
        messages: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            msg = str(err["msg"])
            messages.append(f"{loc}: {msg}" if loc else msg)
        raise typer.BadParameter("\n  ".join(messages)) from exc


def _collect_flat(
    mapping: list[_Leaf],
    kwargs: dict[str, object],
    deferred: set[str],
) -> dict[str, Any]:
    """Re-nest the flat CLI kwargs into the model's input mapping.

    A ``deferred`` parameter still holding ``None`` was never supplied, so its key
    is omitted and Pydantic runs its validated-data ``default_factory`` instead.
    """
    data: dict[str, Any] = {}
    for cli_name, key_path, _, _flags in mapping:
        if cli_name in kwargs:
            if cli_name in deferred and kwargs[cli_name] is None:
                continue
            _set_nested(data, key_path, kwargs[cli_name])
    return data


def _collect_with_config(
    model_cls: type[BaseModel],
    ctx: typer.Context,
    config: Path | None,
    mapping: list[_Leaf],
    kwargs: dict[str, object],
) -> dict[str, Any]:
    """Merge a ``--config`` file (base) with the CLI flags that override it.

    With no ``--config`` every supplied flag is used; with one, the file is the
    base and only explicitly-passed flags (not defaults) override it. Relaxed
    required fields left unset are skipped so Pydantic reports them as missing.
    An unknown key in the file is rejected up front -- a silently-dropped typo
    would let a run proceed with the default in place of the intended value.
    """
    data: dict[str, Any] = {}
    if config is not None:
        try:
            data = dict(load_config_file(config))
        except (OSError, ValueError) as exc:
            # A missing or malformed file is a bad --config value, not a crash:
            # report it the way every other parameter error is reported.
            raise typer.BadParameter(str(exc)) from exc
        unknown = unknown_config_keys(model_cls, data)
        if unknown:
            listed = ", ".join(sorted(unknown))
            msg = f"Unknown setting(s) {listed} in config file {config}"
            raise typer.BadParameter(msg)
    for cli_name, key_path, _, _flags in mapping:
        if cli_name in kwargs and _value_is_explicit(ctx, cli_name):
            _set_nested(data, key_path, kwargs[cli_name])
    return data


def _config_file_params() -> tuple[list[inspect.Parameter], dict[str, object]]:
    """Build the injected ``--config`` / ``--generate-config`` params and context."""
    config_ann = Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="Load settings from a YAML/JSON file (flags passed still override).",
            rich_help_panel=_CONFIG_PANEL,
            show_default="None",
        ),
    ]
    generate_ann = Annotated[
        Path | None,
        typer.Option(
            "--generate-config",
            help="Write a default config template to PATH and exit.",
            rich_help_panel=_CONFIG_PANEL,
            show_default="None",
        ),
    ]
    schema_ann = Annotated[
        bool,
        typer.Option(
            "--schema",
            help="Print the settings model's JSON Schema to stdout and exit.",
            rich_help_panel=_CONFIG_PANEL,
        ),
    ]
    keyword_only = inspect.Parameter.KEYWORD_ONLY
    params = [
        inspect.Parameter(
            _CONFIG_PARAM, keyword_only, default=None, annotation=config_ann,
        ),
        inspect.Parameter(
            _GENERATE_PARAM, keyword_only, default=None, annotation=generate_ann,
        ),
        inspect.Parameter(
            _SCHEMA_PARAM, keyword_only, default=False, annotation=schema_ann,
        ),
        inspect.Parameter(
            _CTX_PARAM, keyword_only, default=None, annotation=typer.Context,
        ),
    ]
    annotations: dict[str, object] = {
        _CONFIG_PARAM: config_ann,
        _GENERATE_PARAM: generate_ann,
        _SCHEMA_PARAM: schema_ann,
        _CTX_PARAM: typer.Context,
    }
    return params, annotations


def _panel_for_field(model_cls: type[BaseModel], field_name: str) -> str | None:
    """Return the help panel title for a field, or ``None`` for the default group.

    The panel is taken from the ``cli_panel`` class attribute of the class that
    *defines* the field -- the most-base class in the MRO whose ``model_fields``
    contains it. Classes that declare no ``cli_panel`` of their own contribute
    no panel, so grouping is fully explicit and opt-in per (mixin) class.

    Args:
        model_cls: The decorated model class.
        field_name: The field to resolve.

    Returns:
        The panel title, or ``None`` if the defining class declares none.
    """
    for klass in reversed(model_cls.__mro__):
        if (
            issubclass(klass, BaseModel)
            and klass is not BaseModel
            and field_name in klass.model_fields
        ):
            panel = klass.__dict__.get("cli_panel")
            return panel if isinstance(panel, str) else None
    return None


def _numeric_type(typer_type: object) -> type | None:
    """Return ``int``/``float`` if ``typer_type`` is that scalar or an optional one.

    ``extract_base_type`` leaves an ``Optional[int]`` as the union ``int | None``,
    so a plain identity check misses it; unwrap a ``T | None`` union to recover
    the numeric member (Typer still applies ``min`` / ``max`` to the option).
    """
    if typer_type is int or typer_type is float:
        return cast("type", typer_type)
    if get_origin(typer_type) in (Union, types.UnionType):
        non_none = [arg for arg in get_args(typer_type) if arg is not type(None)]
        if len(non_none) == 1 and non_none[0] in (int, float):
            return cast("type", non_none[0])
    return None


def _numeric_bounds(field_info: FieldInfo) -> tuple[float | None, float | None]:
    """Extract inclusive ``(min, max)`` bounds from a field's constraints.

    Only ``ge`` / ``le`` (and the ``ge`` / ``le`` of an ``Interval``) map onto
    Typer's inclusive ``min`` / ``max``. Exclusive ``gt`` / ``lt`` bounds are
    left for Pydantic to enforce, since Typer has no exclusive equivalent.

    An ``int`` bound is kept as an ``int``: above 2**53 a float cannot represent
    it exactly, and rounding the bound the wrong way makes Click reject a value
    Pydantic would accept (or vice versa).

    Args:
        field_info: The Pydantic field metadata.

    Returns:
        A ``(min, max)`` tuple; either element is ``None`` when unset.
    """
    low: float | None = None
    high: float | None = None
    for meta in field_info.metadata:
        if isinstance(meta, annotated_types.Ge):
            low = _as_bound(meta.ge)
        elif isinstance(meta, annotated_types.Le):
            high = _as_bound(meta.le)
        elif isinstance(meta, annotated_types.Interval):
            if meta.ge is not None:
                low = _as_bound(meta.ge)
            if meta.le is not None:
                high = _as_bound(meta.le)
    return low, high


def _as_bound(value: object) -> float | None:
    """Coerce a constraint to a Click bound, keeping ``int`` precision intact."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        # A Decimal bound is exact; float() is the only thing Click understands,
        # and Pydantic still enforces the exact bound after parsing.
        return float(value)
    return None


def _factory_takes_data(factory: Callable[..., Any]) -> bool:
    """Whether ``factory`` is a Pydantic 2.10+ factory taking the validated data.

    Such a factory needs the model's other fields, so it can only run inside
    Pydantic -- Click would call it with no arguments and raise ``TypeError``.

    The test mirrors Pydantic's own ``takes_validated_data_argument``: exactly one
    positional parameter with no default. Matching it exactly matters -- a looser
    test would mistake an ordinary one-argument callable (say a class with a
    single ``__init__`` field) for a data-taking factory and drop its value.
    """
    try:
        parameters = list(inspect.signature(factory).parameters.values())
    except (TypeError, ValueError):  # builtins with no introspectable signature
        return False
    if len(parameters) != 1:
        return False
    only = parameters[0]
    return (
        only.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and only.default is inspect.Parameter.empty
    )


def _cli_extra(field_info: FieldInfo) -> dict[str, str]:
    """Read typantic's CLI hints from a field's ``json_schema_extra``.

    Recognised keys: ``cli_name`` (full long flag, e.g. ``"--output"``),
    ``cli_short`` (short flag, e.g. ``"-o"``), and ``cli_envvar`` (environment
    variable name). Non-string values and non-dict ``json_schema_extra`` are
    ignored.

    Args:
        field_info: The Pydantic field metadata.

    Returns:
        A mapping of the recognised hint keys that were present and string-valued.
    """
    raw = field_info.json_schema_extra
    if not isinstance(raw, dict):
        return {}
    extra = cast("dict[str, object]", raw)
    out: dict[str, str] = {}
    for key in ("cli_name", "cli_short", "cli_envvar"):
        value = extra.get(key)
        if isinstance(value, str):
            out[key] = value
    return out


def _option_decls(
    cli_name: str,
    extra: dict[str, str],
    *,
    is_flag: bool = False,
) -> list[str]:
    """Build the ``param_decls`` for a Typer option from CLI hints.

    Returns an empty list when no hints are given, letting Typer derive the
    ``--flag`` from the parameter name. When a short flag is requested the long
    flag must be stated explicitly, so it is always included alongside it.

    Declaring anything explicitly suppresses Click's automatic
    ``--flag / --no-flag`` pair for a boolean, which would leave a ``True``
    default impossible to turn off -- so a boolean's long flag always carries its
    ``/--no-`` counterpart.

    Args:
        cli_name: The (possibly nested) flattened parameter name.
        extra: The parsed CLI hints from :func:`_cli_extra`.
        is_flag: Whether the field is a boolean (needing the off switch).

    Returns:
        The positional declarations to pass to ``typer.Option``.
    """
    long = extra.get("cli_name") or "--" + cli_name.replace("_", "-")
    short = extra.get("cli_short")
    if is_flag and (short or "cli_name" in extra):
        long = f"{long}/--no-{long.removeprefix('--')}"
    if short:
        return [long, short]
    if "cli_name" in extra:
        return [long]
    return []


def _build_params(  # noqa: PLR0913 - a recursive builder; each arg tracks one axis
    model_cls: type[BaseModel],
    *,
    subpanels: bool,
    relax: bool = False,
    prefix: tuple[str, ...] = (),
    key_prefix: tuple[str, ...] = (),
    seen: frozenset[type[BaseModel]] = frozenset(),
    defaults: BaseModel | None = None,
) -> tuple[
    list[inspect.Parameter],
    dict[str, object],
    list[_Leaf],
    set[str],
]:
    """Expand a model's fields into Typer parameters.

    Fields whose type is itself a ``BaseModel`` are flattened recursively: a
    ``db: Database`` field with a ``host`` field becomes a ``--db-host`` option,
    and the values are re-nested before the model is constructed.

    The flag a user types always follows the *field name*, while the value is
    re-nested under the field's *input key* (its alias, when the model is not
    ``populate_by_name``) -- so an aliased model keeps a readable flag and still
    receives the value. See :func:`typantic._introspect.field_input_key`.

    Args:
        model_cls: The model whose fields to expand.
        subpanels: Whether to assign Rich help panels from ``cli_panel``.
        relax: Whether to make required fields optional at the Typer layer (used
            by ``config_file`` mode, where a ``--config`` file may supply them);
            requiredness is then re-checked by Pydantic after merging.
        prefix: The nested path of field *names* leading to this model (the flag).
        key_prefix: The nested path of field *input keys* (the model's kwargs).
        seen: Models already being expanded, to break self-referential cycles.
        defaults: The instance a parent field defaulted to, whose values seed
            this model's parameter defaults. ``None`` uses the class's own field
            defaults.

    Returns:
        A ``(parameters, annotations, mapping, deferred)`` tuple: ``mapping``
        pairs each flattened parameter name with its nested input-key path, and
        ``deferred`` names the parameters whose ``None`` must be dropped so
        Pydantic can run their validated-data ``default_factory``.
    """
    params: list[inspect.Parameter] = []
    annotations: dict[str, object] = {}
    mapping: list[_Leaf] = []
    deferred: set[str] = set()

    resolved_hints = _model_hints(model_cls)
    nested_seen = seen | {model_cls}

    for name, field_info in model_cls.model_fields.items():
        base_type = _extract_base_type(resolved_hints[name])
        path = (*prefix, name)
        key_path = (*key_prefix, _field_input_key(model_cls, name, field_info))
        # A parent default instance supplies this field's value; otherwise fall
        # back to the field's own default.
        override = getattr(defaults, name) if defaults is not None else _MISSING

        if _is_model_type(base_type) and base_type not in nested_seen:
            sub = _build_params(
                base_type,
                subpanels=subpanels,
                relax=relax,
                prefix=path,
                key_prefix=key_path,
                seen=nested_seen,
                defaults=_nested_default(field_info, override),
            )
            params.extend(sub[0])
            annotations.update(sub[1])
            mapping.extend(sub[2])
            deferred |= sub[3]
            continue

        cli_name = "_".join(path)
        panel = _panel_for_field(model_cls, name) if subpanels else None
        param, annotated, flags = _build_leaf(
            cli_name=cli_name,
            field_info=field_info,
            base_type=base_type,
            panel=panel,
            relax=relax,
            default_override=override,
        )
        params.append(param)
        annotations[cli_name] = annotated
        mapping.append(_Leaf(cli_name, key_path, path, flags))
        if (
            override is _MISSING
            and not field_info.is_required()
            and field_info.default_factory is not None
            and _factory_takes_data(field_info.default_factory)
        ):
            deferred.add(cli_name)

    return params, annotations, mapping, deferred


def _nested_default(field_info: FieldInfo, override: object) -> BaseModel | None:
    """The model instance a nested field defaults to, if it has one.

    A ``db: Database = Database(host="prod")`` default must win over
    ``Database``'s own field defaults -- otherwise the CLI silently advertises
    (and submits) ``host="localhost"``. A parent ``override`` takes precedence,
    since it is already the resolved value for this field.

    A ``default_factory`` is evaluated once here, so its instance seeds the
    flattened options too. The flattened form has to show *some* value, and the
    factory's is the true one; the alternative -- ignoring it -- is what silently
    substituted the inner class's defaults.
    """
    if override is not _MISSING:
        # Reached only for a concrete nested model, so a parent's default instance
        # always holds a real instance here.
        return cast("BaseModel", override)
    default = field_info.get_default(call_default_factory=False)
    if isinstance(default, BaseModel):
        return default
    factory = field_info.default_factory
    if factory is not None and not _factory_takes_data(factory):
        made = cast("Callable[[], object]", factory)()
        if isinstance(made, BaseModel):
            return made
    return None


def _check_name_collisions(mapping: list[_Leaf]) -> None:
    """Raise a clear error if two fields claim the same parameter name or flag.

    A nested field's CLI name is its path joined by ``_`` (``db.host`` becomes
    ``db_host``), which can collide with a sibling field literally named
    ``db_host``. Left unchecked this surfaces as an opaque ``inspect.Signature``
    ``ValueError`` at decoration time; name the offending fields instead.

    Two fields can also claim the same *flag* through ``cli_name`` / ``cli_short``
    even when their parameter names differ. Click keeps only the last such option,
    so the other field would silently stop being settable -- name them too.
    """
    seen: dict[str, tuple[str, ...]] = {}
    flags: dict[str, tuple[str, ...]] = {}
    for cli_name, _, name_path, declared in mapping:
        if cli_name in seen:
            first = ".".join(seen[cli_name])
            second = ".".join(name_path)
            flag = "--" + cli_name.replace("_", "-")
            msg = (
                f"CLI name collision: fields '{first}' and '{second}' both flatten "
                f"to parameter '{cli_name}' ({flag}). Rename one of the fields."
            )
            raise ValueError(msg)
        seen[cli_name] = name_path

        for flag in declared:
            if flag in flags:
                first = ".".join(flags[flag])
                second = ".".join(name_path)
                msg = (
                    f"CLI flag collision: fields '{first}' and '{second}' both "
                    f"declare '{flag}'. Change one field's cli_name/cli_short."
                )
                raise ValueError(msg)
            flags[flag] = name_path


def _declared_flags(
    cli_name: str,
    decls: list[str],
    *,
    is_argument: bool,
) -> tuple[str, ...]:
    """The flags a leaf claims, including the long flag Typer derives implicitly.

    An argument is positional and claims none. A boolean's ``--x/--no-x`` decl
    covers two flags, so it is split -- otherwise ``--no-x`` could silently
    collide with a sibling's flag.
    """
    if is_argument:
        return ()
    # No explicit decls: Typer derives the long flag from the parameter name.
    flags = decls or ["--" + cli_name.replace("_", "-")]
    return tuple(part for decl in flags for part in decl.split("/"))


def _secret_type(base_type: object) -> type | None:
    """Return the secret scalar in ``base_type``, unwrapping a ``T | None`` union.

    ``extract_base_type`` leaves an ``Optional[SecretStr]`` as the union
    ``SecretStr | None``, so a plain identity check misses it and the raw
    ``SecretStr`` reaches Typer, which cannot render it.
    """
    for candidate in _union_members(base_type):
        if candidate is SecretStr or candidate is SecretBytes:
            return cast("type", candidate)
    return None


def _union_members(typer_type: object) -> tuple[object, ...]:
    """``typer_type`` itself, or the non-``None`` members of a ``T | None`` union."""
    if get_origin(typer_type) in (Union, types.UnionType):
        return tuple(arg for arg in get_args(typer_type) if arg is not type(None))
    return (typer_type,)


def _is_bool(typer_type: object) -> bool:
    """Whether Click will render this as a boolean flag (optional bools included)."""
    return _union_members(typer_type) == (bool,)


def _build_leaf(  # noqa: PLR0913 - one leaf's full context; all keyword-only
    *,
    cli_name: str,
    field_info: FieldInfo,
    base_type: object,
    panel: str | None,
    relax: bool = False,
    default_override: object = _MISSING,
) -> tuple[inspect.Parameter, object, tuple[str, ...]]:
    """Build the ``inspect.Parameter``, annotation and flags for one leaf field.

    Args:
        cli_name: The flattened parameter name (nested path joined by ``_``).
        field_info: The Pydantic field metadata.
        base_type: The structural type extracted from the field annotation.
        panel: The Rich help panel title for options, or ``None`` for none.
        relax: Whether to make a required field optional at the Typer layer, so it
            can be supplied via ``--config``.
        default_override: A value from a parent field's default instance, which
            wins over the field's own default (and makes it optional). ``_MISSING``
            when the field's own default applies.

    Returns:
        A ``(parameter, annotation)`` pair for the rewritten signature.
    """
    help_text = field_info.description or ""
    extra = _cli_extra(field_info)
    envvar = extra.get("cli_envvar")

    secret = _secret_type(base_type)
    is_secret = secret is not None
    typer_type: object = base_type
    if is_secret:
        # Typer renders neither SecretStr nor bytes; a secret is entered as text.
        typer_type = str

    if _numeric_type(typer_type) is not None:
        min_value, max_value = _numeric_bounds(field_info)
    else:
        min_value = max_value = None

    overridden = default_override is not _MISSING
    required = field_info.is_required() and not overridden
    relaxed_required = required and relax
    default: object
    show_default: bool | str
    if relaxed_required:
        # Optional at the Typer layer; whether it was actually supplied is decided
        # by the parameter source (not the value), and Pydantic re-checks
        # requiredness after the --config merge.
        default = None
        show_default = False
    elif required:
        default = inspect.Parameter.empty
        show_default = True
    elif overridden:
        # A parent field's default instance supplies this value; it wins over the
        # nested class's own field default.
        default = default_override
        show_default = "None" if default is None else True
    elif field_info.default_factory is not None and _factory_takes_data(
        field_info.default_factory,
    ):
        # A factory taking the validated data cannot run at the Click layer -- it
        # needs the model's other fields. Default to None and drop the key when it
        # is still None, so Pydantic runs the factory itself; handing Click the
        # callable would make it call a 1-arg factory with none (TypeError).
        default = None
        show_default = "computed at runtime"
    elif field_info.default_factory is not None:
        # Pass the factory itself as the default so Click re-evaluates it on
        # every invocation (correct for time/identity-sensitive factories such
        # as ``datetime.now`` or ``uuid4``). A single frozen evaluation would be a
        # misleading help sample for those, so show a sentinel instead.
        default = field_info.default_factory
        show_default = "computed at runtime"
    else:
        default = field_info.default
        # Click omits `None` defaults entirely; surface them as
        # "[default: (None)]" so optional options are visibly so.
        show_default = "None" if default is None else True

    is_argument = field_info.kw_only is False
    decls = _option_decls(cli_name, extra, is_flag=_is_bool(typer_type))
    typer_meta: typer.models.ArgumentInfo | typer.models.OptionInfo
    if is_argument:
        typer_meta = typer.Argument(
            help=help_text,
            show_default=False,
            min=min_value,
            max=max_value,
            envvar=envvar,
        )
    else:
        typer_meta = typer.Option(
            *decls,
            help=help_text,
            rich_help_panel=panel,
            show_default=False if is_secret else show_default,
            hide_input=is_secret,
            prompt=is_secret and required and not relaxed_required,
            min=min_value,
            max=max_value,
            envvar=envvar,
        )
    kind = (
        inspect.Parameter.POSITIONAL_OR_KEYWORD
        if is_argument
        else inspect.Parameter.KEYWORD_ONLY
    )

    annotated = Annotated[typer_type, typer_meta]  # type: ignore[valid-type]
    parameter = inspect.Parameter(
        cli_name,
        kind,
        default=default,
        annotation=annotated,
    )
    flags = _declared_flags(cli_name, decls, is_argument=is_argument)
    return parameter, annotated, flags


def pydantic_to_typer(
    model_cls: type[BaseModel],
    *,
    subpanels: bool = False,
    config_file: bool | Literal["only"] = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Rewrite a function's signature so Typer sees individual CLI params.

    The parameters are derived from the fields of ``model_cls``.

    Mapping rules:
        - ``kw_only=False``  ->  ``typer.Argument``
        - ``kw_only=True`` (or unset)  ->  ``typer.Option``
        - ``Field(description=...)``  ->  ``help=...``
        - ``Field(default=...)``  ->  Typer default value
        - ``Field(default_factory=...)``  ->  the factory is passed through to
          Click as a callable default, so it runs once per invocation;
          ``--help`` shows ``[default: (computed at runtime)]`` rather than a
          frozen sample. A factory taking the validated data (pydantic 2.10+)
          is left for Pydantic to run instead
        - ``Field(alias=...)`` / ``validation_alias`` / ``alias_generator``  ->
          the flag still follows the field *name*; the value is submitted under
          the alias, so an aliased model works unchanged
        - ``Field(ge=..., le=...)``  ->  Typer ``min`` / ``max`` (exclusive
          ``gt`` / ``lt`` are left to Pydantic)
        - ``SecretStr`` / ``SecretBytes``  ->  hidden input (and a secure
          prompt when the field is required)
        - a ``None`` default  ->  rendered as ``[default: (None)]`` in
          ``--help`` (Click would otherwise omit it entirely)
        - a nested ``BaseModel`` field  ->  flattened into prefixed params
          (``db: Database`` with a ``host`` field becomes ``--db-host``)

    Per-field CLI hints can be supplied via ``Field(json_schema_extra=...)``:
        - ``cli_name``: full long flag, e.g. ``"--output"``
        - ``cli_short``: short flag, e.g. ``"-o"``
        - ``cli_envvar``: environment variable to read the value from

    The decorated function receives the **validated** Pydantic model
    instance, so all ``AfterValidator`` / ``BeforeValidator`` logic runs
    as usual. Validators that raise ``ValueError`` / ``AssertionError`` are
    reported as Typer parameter errors; other exception types propagate
    unchanged.

    Args:
        model_cls: The Pydantic model class whose fields define the CLI
            parameters.
        subpanels: Group options into Rich help panels. Each option is placed
            in the panel named by the ``cli_panel`` class attribute of the
            class that defines its field (useful for models composed from
            mixins). Fields whose defining class declares no ``cli_panel``
            stay in the default options group. Arguments are never panelled.
        config_file: Add file-driven config support. Three options are injected:
            ``--config PATH`` loads settings from a YAML/JSON file (used as the
            base; any explicitly-passed flags override it),
            ``--generate-config PATH`` writes an editable default template and
            exits without running, and ``--schema`` prints the model's JSON
            Schema to stdout and exits (so a web front-end can build a form from
            the model by subprocessing the CLI, without importing it). To let
            ``--config`` supply them, required
            fields are made optional at the Typer layer and re-checked by
            Pydantic after merge (so they no longer render as ``[required]``).
            Pass ``"only"`` for a **file-only** command: no per-field flags are
            generated at all (just ``--config`` / ``--generate-config``), for
            models that cannot map onto flat flags (nested-model lists,
            ``scalar | (min, max)`` ranges).

    Returns:
        A decorator that transforms a ``func(model)`` signature into one
        that Typer can introspect.

    Example:
        >>> import typer
        >>> app = typer.Typer()
        >>> @app.command()
        ... @pydantic_to_typer(MyConfig, subpanels=True)
        ... def run(config: MyConfig): ...
    """
    file_only = config_file == "only"

    def decorator(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        if file_only:
            new_params: list[inspect.Parameter] = []
            new_annotations: dict[str, object] = {}
            mapping: list[_Leaf] = []
            deferred: set[str] = set()
        else:
            new_params, new_annotations, mapping, deferred = _build_params(
                model_cls,
                subpanels=subpanels,
                relax=bool(config_file),
            )
            _check_name_collisions(mapping)
            new_params.sort(
                key=lambda p: (
                    p.kind == inspect.Parameter.KEYWORD_ONLY,
                    p.default is not inspect.Parameter.empty,
                ),
            )

        if config_file:
            extra_params, extra_annotations = _config_file_params()
            new_params.extend(extra_params)
            new_annotations.update(extra_annotations)

        @wraps(func)
        def wrapper(**kwargs: object) -> object:
            if config_file:
                ctx = cast("typer.Context", kwargs.pop(_CTX_PARAM))
                generate = cast("Path | None", kwargs.pop(_GENERATE_PARAM))
                config = cast("Path | None", kwargs.pop(_CONFIG_PARAM))
                schema = cast("bool", kwargs.pop(_SCHEMA_PARAM))
                if schema:
                    # The web front-end subprocesses this to build a form from the
                    # model without importing it (keeping torch out of its process).
                    typer.echo(json.dumps(model_cls.model_json_schema(), indent=2))
                    raise typer.Exit
                if generate is not None and config is not None:
                    # Without this, generation would silently win and the run be
                    # skipped; the two are mutually exclusive.
                    msg = "Pass either --config or --generate-config, not both."
                    raise typer.BadParameter(msg)
                if generate is not None:
                    write_config_template(model_cls, generate)
                    typer.echo(f"Wrote config template to {generate}.")
                    raise typer.Exit
                if file_only and config is None:
                    msg = (
                        "Provide --config FILE, or --generate-config FILE "
                        "to create one."
                    )
                    raise typer.BadParameter(msg)
                data = _collect_with_config(model_cls, ctx, config, mapping, kwargs)
            else:
                data = _collect_flat(mapping, kwargs, deferred)
            return func(_construct(model_cls, data))

        wrapper.__signature__ = inspect.Signature(new_params)  # type: ignore[attr-defined]
        wrapper.__annotations__ = new_annotations
        return wrapper

    return decorator


def add_command[ModelT: BaseModel](  # noqa: PLR0913
    app: typer.Typer,
    model_cls: type[ModelT],
    handler: Callable[[ModelT], Any],
    *,
    name: str | None = None,
    subpanels: bool = False,
    config_file: bool | Literal["only"] = False,
    help: str | None = None,  # noqa: A002 - matches Typer's own parameter name
) -> None:
    """Register ``handler`` on ``app`` as a command driven by ``model_cls``.

    A convenience wrapper around :func:`pydantic_to_typer` and
    ``app.command`` that removes the boilerplate of decorating a stub function.

    Args:
        app: The Typer application to register the command on.
        model_cls: The Pydantic model whose fields define the CLI parameters.
        handler: A function accepting the validated model instance.
        name: The command name. Defaults to ``handler.__name__``.
        subpanels: Forwarded to :func:`pydantic_to_typer`.
        config_file: Forwarded to :func:`pydantic_to_typer` -- add
            ``--config`` / ``--generate-config`` / ``--schema`` support
            (``"only"`` for a file-only command with no per-field flags).
        help: Command help text. Defaults to the handler's docstring.

    Example:
        >>> import typer
        >>> app = typer.Typer()
        >>> def run(config: MyConfig) -> None: ...
        >>> add_command(app, MyConfig, run)
    """
    decorated = pydantic_to_typer(
        model_cls,
        subpanels=subpanels,
        config_file=config_file,
    )(handler)
    app.command(name=name, help=help)(decorated)
