"""Core decorator for converting Pydantic models to Typer CLI interfaces."""

import inspect
import types
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

import typer
from pydantic import BaseModel, ValidationError


def _extract_base_type(annotation: object) -> object:
    """Strip ``Annotated`` validator metadata, keeping the structural type.

    Recursively walks through ``Annotated``, ``Union``, ``list``, and
    ``tuple`` wrappers, discarding everything except the base types that
    Typer can interpret.

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


def pydantic_to_typer(
    model_cls: type[BaseModel],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Rewrite a function's signature so Typer sees individual CLI params.

    The parameters are derived from the fields of ``model_cls``.

    Mapping rules:
        - ``kw_only=False``  ->  ``typer.Argument``
        - ``kw_only=True`` (or unset)  ->  ``typer.Option``
        - ``Field(description=...)``  ->  ``help=...``
        - ``Field(default=...)``  ->  Typer default value
        - ``Field(default_factory=...)``  ->  factory is called once at
          decoration time to supply the Typer default

    The decorated function receives the **validated** Pydantic model
    instance, so all ``AfterValidator`` / ``BeforeValidator`` logic runs
    as usual.

    Args:
        model_cls: The Pydantic model class whose fields define the CLI
            parameters.

    Returns:
        A decorator that transforms a ``func(model)`` signature into one
        that Typer can introspect.

    Example:
        >>> import typer
        >>> app = typer.Typer()
        >>> @app.command()
        ... @pydantic_to_typer(MyConfig)
        ... def run(config: MyConfig): ...
    """

    def decorator(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        new_params: list[inspect.Parameter] = []
        new_annotations: dict[str, object] = {}

        resolved_hints = get_type_hints(
            model_cls,
            include_extras=True,
        )

        for name, field_info in model_cls.model_fields.items():
            base_type = _extract_base_type(resolved_hints[name])
            help_text = field_info.description or ""

            typer_meta: typer.models.ArgumentInfo | typer.models.OptionInfo
            if field_info.kw_only is False:
                typer_meta = typer.Argument(
                    help=help_text,
                    show_default=False,
                )
            else:
                typer_meta = typer.Option(help=help_text)

            annotated = Annotated[base_type, typer_meta]  # type: ignore[valid-type]
            new_annotations[name] = annotated

            default: object
            if field_info.is_required():
                default = inspect.Parameter.empty
            elif field_info.default_factory is not None:
                default = field_info.default_factory()  # type: ignore[call-arg]
            else:
                default = field_info.default

            new_params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD
                    if field_info.kw_only is False
                    else inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=annotated,
                ),
            )

        new_params.sort(
            key=lambda p: (
                p.kind == inspect.Parameter.KEYWORD_ONLY,
                p.default is not inspect.Parameter.empty,
            ),
        )

        @wraps(func)
        def wrapper(**kwargs: object) -> object:
            try:
                model = model_cls(**kwargs)
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
