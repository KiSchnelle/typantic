"""Core decorator for converting Pydantic models to Typer CLI interfaces."""

import inspect
import types
from collections.abc import Callable
from functools import wraps
from typing import (
    Annotated,
    Any,
    TypeGuard,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

import annotated_types
import typer
from pydantic import BaseModel, SecretBytes, SecretStr, ValidationError
from pydantic.fields import FieldInfo


def _extract_base_type(annotation: object) -> object:
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
        >>> _extract_base_type(Annotated[float, Field(description="x")])
        <class 'float'>
    """
    if get_origin(annotation) is Annotated:
        inner = get_args(annotation)[0]
        return _extract_base_type(inner)

    if get_origin(annotation) in (Union, types.UnionType):
        cleaned = tuple(_extract_base_type(a) for a in get_args(annotation))
        return Union[cleaned]  # noqa: UP007

    if get_origin(annotation) is list:
        args = get_args(annotation)
        if args:
            return list[_extract_base_type(args[0])]  # type: ignore[misc]

    if get_origin(annotation) is tuple:
        args = get_args(annotation)
        if args:
            cleaned = tuple(_extract_base_type(a) for a in args)
            return tuple[cleaned]  # type: ignore[valid-type]

    return annotation


def _is_model_type(tp: object) -> TypeGuard[type[BaseModel]]:
    """Return ``True`` if ``tp`` is a concrete ``BaseModel`` subclass."""
    return isinstance(tp, type) and issubclass(tp, BaseModel) and tp is not BaseModel


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


def _numeric_bounds(field_info: FieldInfo) -> tuple[float | None, float | None]:
    """Extract inclusive ``(min, max)`` bounds from a field's constraints.

    Only ``ge`` / ``le`` (and the ``ge`` / ``le`` of an ``Interval``) map onto
    Typer's inclusive ``min`` / ``max``. Exclusive ``gt`` / ``lt`` bounds are
    left for Pydantic to enforce, since Typer has no exclusive equivalent.

    Args:
        field_info: The Pydantic field metadata.

    Returns:
        A ``(min, max)`` tuple; either element is ``None`` when unset.
    """
    low: float | None = None
    high: float | None = None
    for meta in field_info.metadata:
        if isinstance(meta, annotated_types.Ge):
            low = float(meta.ge)  # type: ignore[arg-type]
        elif isinstance(meta, annotated_types.Le):
            high = float(meta.le)  # type: ignore[arg-type]
        elif isinstance(meta, annotated_types.Interval):
            if meta.ge is not None:
                low = float(meta.ge)  # type: ignore[arg-type]
            if meta.le is not None:
                high = float(meta.le)  # type: ignore[arg-type]
    return low, high


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


def _option_decls(cli_name: str, extra: dict[str, str]) -> list[str]:
    """Build the ``param_decls`` for a Typer option from CLI hints.

    Returns an empty list when no hints are given, letting Typer derive the
    ``--flag`` from the parameter name. When a short flag is requested the long
    flag must be stated explicitly, so it is always included alongside it.

    Args:
        cli_name: The (possibly nested) flattened parameter name.
        extra: The parsed CLI hints from :func:`_cli_extra`.

    Returns:
        The positional declarations to pass to ``typer.Option``.
    """
    long = extra.get("cli_name") or "--" + cli_name.replace("_", "-")
    short = extra.get("cli_short")
    if short:
        return [long, short]
    if "cli_name" in extra:
        return [long]
    return []


def _build_params(
    model_cls: type[BaseModel],
    *,
    subpanels: bool,
    prefix: tuple[str, ...] = (),
    seen: frozenset[type[BaseModel]] = frozenset(),
) -> tuple[
    list[inspect.Parameter],
    dict[str, object],
    list[tuple[str, tuple[str, ...]]],
]:
    """Expand a model's fields into Typer parameters.

    Fields whose type is itself a ``BaseModel`` are flattened recursively: a
    ``db: Database`` field with a ``host`` field becomes a ``--db-host`` option,
    and the values are re-nested before the model is constructed.

    Args:
        model_cls: The model whose fields to expand.
        subpanels: Whether to assign Rich help panels from ``cli_panel``.
        prefix: The nested path of field names leading to this model.
        seen: Models already being expanded, to break self-referential cycles.

    Returns:
        A ``(parameters, annotations, mapping)`` tuple, where ``mapping`` pairs
        each flattened parameter name with its nested path into the model.
    """
    params: list[inspect.Parameter] = []
    annotations: dict[str, object] = {}
    mapping: list[tuple[str, tuple[str, ...]]] = []

    resolved_hints = get_type_hints(model_cls, include_extras=True)
    nested_seen = seen | {model_cls}

    for name, field_info in model_cls.model_fields.items():
        base_type = _extract_base_type(resolved_hints[name])
        path = (*prefix, name)

        if _is_model_type(base_type) and base_type not in nested_seen:
            sub_params, sub_annotations, sub_mapping = _build_params(
                base_type,
                subpanels=subpanels,
                prefix=path,
                seen=nested_seen,
            )
            params.extend(sub_params)
            annotations.update(sub_annotations)
            mapping.extend(sub_mapping)
            continue

        cli_name = "_".join(path)
        panel = _panel_for_field(model_cls, name) if subpanels else None
        param, annotated = _build_leaf(
            cli_name=cli_name,
            field_info=field_info,
            base_type=base_type,
            panel=panel,
        )
        params.append(param)
        annotations[cli_name] = annotated
        mapping.append((cli_name, path))

    return params, annotations, mapping


def _build_leaf(
    *,
    cli_name: str,
    field_info: FieldInfo,
    base_type: object,
    panel: str | None,
) -> tuple[inspect.Parameter, object]:
    """Build the ``inspect.Parameter`` and annotation for a single leaf field.

    Args:
        cli_name: The flattened parameter name (nested path joined by ``_``).
        field_info: The Pydantic field metadata.
        base_type: The structural type extracted from the field annotation.
        panel: The Rich help panel title for options, or ``None`` for none.

    Returns:
        A ``(parameter, annotation)`` pair for the rewritten signature.
    """
    help_text = field_info.description or ""
    extra = _cli_extra(field_info)
    envvar = extra.get("cli_envvar")

    is_secret = base_type is SecretStr or base_type is SecretBytes
    typer_type: object = base_type
    if is_secret:
        typer_type = bytes if base_type is SecretBytes else str

    if typer_type is int or typer_type is float:
        min_value, max_value = _numeric_bounds(field_info)
    else:
        min_value = max_value = None

    required = field_info.is_required()
    default: object
    show_default: bool | str
    if required:
        default = inspect.Parameter.empty
        show_default = True
    elif field_info.default_factory is not None:
        factory = field_info.default_factory
        # Pass the factory itself as the default so Click re-evaluates it on
        # every invocation (correct for time/identity-sensitive factories such
        # as ``datetime.now`` or ``uuid4``), while still showing a sample value.
        default = factory
        show_default = str(factory())  # type: ignore[call-arg]
    else:
        default = field_info.default
        # Click omits `None` defaults entirely; surface them as
        # "[default: (None)]" so optional options are visibly so.
        show_default = "None" if default is None else True

    is_argument = field_info.kw_only is False
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
            *_option_decls(cli_name, extra),
            help=help_text,
            rich_help_panel=panel,
            show_default=False if is_secret else show_default,
            hide_input=is_secret,
            prompt=is_secret and required,
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
    return parameter, annotated


def pydantic_to_typer(
    model_cls: type[BaseModel],
    *,
    subpanels: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Rewrite a function's signature so Typer sees individual CLI params.

    The parameters are derived from the fields of ``model_cls``.

    Mapping rules:
        - ``kw_only=False``  ->  ``typer.Argument``
        - ``kw_only=True`` (or unset)  ->  ``typer.Option``
        - ``Field(description=...)``  ->  ``help=...``
        - ``Field(default=...)``  ->  Typer default value
        - ``Field(default_factory=...)``  ->  the factory is passed through to
          Click as a callable default, so it runs once per invocation (and a
          sample value is shown in ``--help``)
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

    def decorator(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        new_params, new_annotations, mapping = _build_params(
            model_cls,
            subpanels=subpanels,
        )

        new_params.sort(
            key=lambda p: (
                p.kind == inspect.Parameter.KEYWORD_ONLY,
                p.default is not inspect.Parameter.empty,
            ),
        )

        @wraps(func)
        def wrapper(**kwargs: object) -> object:
            data: dict[str, Any] = {}
            for cli_name, path in mapping:
                if cli_name not in kwargs:
                    continue
                target = data
                for part in path[:-1]:
                    target = target.setdefault(part, {})
                target[path[-1]] = kwargs[cli_name]

            try:
                model = model_cls(**data)
            except ValidationError as exc:
                messages: list[str] = []
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    msg = str(err["msg"])
                    messages.append(f"{loc}: {msg}" if loc else msg)
                raise typer.BadParameter("\n  ".join(messages)) from exc
            return func(model)

        wrapper.__signature__ = inspect.Signature(new_params)  # type: ignore[attr-defined]
        wrapper.__annotations__ = new_annotations
        return wrapper

    return decorator


def add_command[ModelT: BaseModel](
    app: typer.Typer,
    model_cls: type[ModelT],
    handler: Callable[[ModelT], Any],
    *,
    name: str | None = None,
    subpanels: bool = False,
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

    Example:
        >>> import typer
        >>> app = typer.Typer()
        >>> def run(config: MyConfig) -> None: ...
        >>> add_command(app, MyConfig, run)
    """
    decorated = pydantic_to_typer(model_cls, subpanels=subpanels)(handler)
    app.command(name=name)(decorated)
