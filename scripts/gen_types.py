"""Generate ``web/src/types.ts`` from the Pydantic models in ``models.py``.

The dashboard's TypeScript types are a projection of the web API's Pydantic
models. Rather than mirror them by hand (which silently drifts — a field added
to a model but not to ``types.ts`` goes unnoticed until something breaks), this
emits the whole file from ``typantic.web.models``.

Usage::

    python scripts/gen_types.py            # (re)write web/src/types.ts
    python scripts/gen_types.py --check     # exit 1 if the file is out of date

The ``--check`` form is the CI drift guard. A couple of frontend-only shapes
(``JsonSchema``, ``JobQuery``) have no Pydantic source and are emitted verbatim
from ``STATIC`` below.
"""

import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from typantic.web.models import (
    TERMINAL_STATUSES,
    ApiMeta,
    BackendMeta,
    CommandMeta,
    FsEntry,
    FsListing,
    History,
    JobImage,
    JobPage,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    Project,
    ProjectGroup,
)

_TYPES_TS = Path(__file__).resolve().parent.parent / "web" / "src" / "types.ts"

# Emitted in this order. Interface order is irrelevant to TypeScript (declarations
# hoist), so this is just for readability.
_MODELS: tuple[type[BaseModel], ...] = (
    CommandMeta,
    BackendMeta,
    ApiMeta,
    LaunchRequest,
    LaunchPreview,
    JobRecord,
    JobPage,
    Project,
    ProjectGroup,
    History,
    FsEntry,
    FsListing,
    JobImage,
)

_HEADER = """\
// AUTO-GENERATED from typantic/web/models.py by scripts/gen_types.py.
// Do not edit by hand: run `make gen-types` after changing the models.
// `python scripts/gen_types.py --check` fails CI if this file is out of date."""

# Frontend-only shapes with no Pydantic source: a schema alias handed to RJSF and
# the jobs-list query params (built client-side, never sent by the server).
_STATIC = """\
// A JSON Schema object (a command's --schema, or a backend's options); handed
// straight to RJSF.
export type JsonSchema = Record<string, unknown>;

// The jobs-list query params, assembled client-side (no server model).
export interface JobQuery {
  status?: JobStatus | "";
  app?: string;
  backend?: string;
  project?: string;
  ungrouped?: boolean;
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}"""

_SCALARS: dict[object, str] = {
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    datetime: "string",
    type(None): "null",
    Any: "unknown",
}


def ts_type(tp: object) -> str:
    """Map a Python annotation to its TypeScript type expression."""
    origin = get_origin(tp)
    if origin is None:
        return _ts_scalar(tp)
    if origin in (list, set, frozenset):
        (arg,) = get_args(tp)
        return f"{ts_type(arg)}[]"
    if origin is tuple:  # only variadic tuple[X, ...] is used
        return f"{ts_type(get_args(tp)[0])}[]"
    if origin is dict:
        return "Record<string, unknown>"
    if origin in (Union, UnionType):
        args = get_args(tp)
        parts = [ts_type(a) for a in args if a is not type(None)]
        if type(None) in args:
            parts.append("null")
        return " | ".join(parts)
    msg = f"Unmapped generic annotation: {tp!r}"
    raise TypeError(msg)


def _ts_scalar(tp: object) -> str:
    mapped = _SCALARS.get(tp)
    if mapped is not None:
        return mapped
    if isinstance(tp, type) and issubclass(tp, (Enum, BaseModel)):
        return tp.__name__
    msg = f"Unmapped annotation: {tp!r}"
    raise TypeError(msg)


def _interface(model: type[BaseModel]) -> str:
    lines = [f"export interface {model.__name__} {{"]
    for name, field in model.model_fields.items():
        lines.append(f"  {name}: {ts_type(field.annotation)};")
    for name, computed in model.model_computed_fields.items():
        lines.append(f"  {name}: {ts_type(computed.return_type)};")
    lines.append("}")
    return "\n".join(lines)


def _enum() -> str:
    values = " | ".join(f'"{member.value}"' for member in JobStatus)
    terminals = ", ".join(
        f'"{member.value}"' for member in JobStatus if member in TERMINAL_STATUSES
    )
    return (
        f"export type JobStatus = {values};\n\n"
        f"export const TERMINAL_STATUSES: JobStatus[] = [{terminals}];"
    )


def render() -> str:
    """Return the full generated ``types.ts`` content."""
    blocks = [_HEADER, _enum(), _STATIC, *(_interface(m) for m in _MODELS)]
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    """Write ``types.ts`` (or, with ``--check``, verify it is up to date)."""
    content = render()
    if "--check" in sys.argv[1:]:
        current = _TYPES_TS.read_text() if _TYPES_TS.exists() else ""
        if current != content:
            sys.stdout.write(
                f"{_TYPES_TS} is out of date. Run `make gen-types` and commit.\n",
            )
            return 1
        sys.stdout.write(f"{_TYPES_TS} is up to date.\n")
        return 0
    _TYPES_TS.write_text(content)
    sys.stdout.write(f"Wrote {_TYPES_TS}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
