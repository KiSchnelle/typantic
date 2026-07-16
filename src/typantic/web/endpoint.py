"""``add_endpoint``: the in-process web analog of ``add_command``.

Register a POST endpoint that validates the request body into a Pydantic model
and calls a handler with the validated instance — the FastAPI mirror of
typantic's Pydantic → Typer CLI bridge — plus a ``GET {path}/schema`` route that
emits the model's form-ready JSON Schema. Unlike the launcher, this runs the
handler *in process*, so it is the simple case for a small app that wants a web
form over the same settings model it already defined.
"""

from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI
from pydantic import BaseModel

from typantic.web.schema import normalize_for_form


def add_endpoint[ModelT: BaseModel](
    app: FastAPI,
    model_cls: type[ModelT],
    handler: Callable[[ModelT], Any],
    *,
    name: str | None = None,
    path: str | None = None,
) -> None:
    """Register ``POST {path}`` (validate body → handler) and ``GET {path}/schema``.

    Args:
        app: The FastAPI app to register the routes on.
        model_cls: The Pydantic settings model; the POST body is validated into it.
        handler: Called with the validated model instance; its return is the
            response body.
        name: Route name (defaults to the handler's ``__name__``).
        path: Route path (defaults to ``/{name}``).
    """
    route = path or f"/{name or handler.__name__}"

    def run(payload: Any) -> Any:  # noqa: ANN401 - body type is set on __annotations__
        return handler(payload)

    # FastAPI reads the annotation to treat the body as ``model_cls`` (and to
    # validate it, returning 422 on failure). Set it dynamically since the model
    # is only known at call time.
    run.__annotations__["payload"] = model_cls

    def schema() -> dict[str, object]:
        normalized = normalize_for_form(model_cls.model_json_schema())
        return cast("dict[str, object]", normalized)

    app.post(route)(run)
    app.get(f"{route}/schema")(schema)
