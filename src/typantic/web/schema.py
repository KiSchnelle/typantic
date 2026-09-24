"""Fetch each command's JSON Schema by subprocessing the CLI (not importing it).

A command's form is derived from its settings model's JSON Schema. Rather than
import the model — which may pull in heavy runtime dependencies — the gateway
runs ``<app> <argv> --schema`` (added generically by typantic's config-file
support) and reads the JSON from stdout. The heavy import happens in the CLI's
own process; the web process only ever sees JSON.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import cast

from typantic.web._subprocess import run_tool
from typantic.web.models import CommandMeta

# Importing a heavy settings module can be slow the first time.
_SCHEMA_TIMEOUT_S = 120.0
# How much of a failing app's stderr goes into the error: its tail, where the
# traceback's last line is, not the whole of it.
_STDERR_TAIL = 4000

# Scalar JSON Schema types a nullable field can keep as ``type: [X, "null"]``
# when its union is collapsed. Array/object/$ref branches are excluded: RJSF's
# form logic keys off a string ``type`` (``"array"``/``"object"``), so a type
# array would silently change how those render.
_NULLABLE_SCALAR_TYPES = frozenset({"number", "integer", "string", "boolean"})


class SchemaError(RuntimeError):
    """Raised when a command's ``--schema`` invocation fails or is unparseable."""


# Path, modification time (ns) and size of an app's executable.
type _Fingerprint = tuple[str, int, int]


def _app_fingerprint(meta: CommandMeta) -> _Fingerprint | None:
    """Identify the installed app behind a command by its executable's stat.

    Installing or upgrading an app rewrites its console script, so a different
    fingerprint means the app the schema came from has been replaced. ``None``
    when the executable is missing or cannot be read.
    """
    executable = shutil.which(meta.app)
    if executable is None:
        return None
    try:
        stat = Path(executable).stat()
    except OSError:
        return None
    return (executable, stat.st_mtime_ns, stat.st_size)


class SchemaCache:
    """Lazily fetches and caches each command's JSON Schema by command key.

    A schema is stable for an installed app, so it is kept for as long as the
    app's executable is unchanged; installing or upgrading the app rewrites that
    script, and the next request fetches the schema afresh. Source edits to an
    editable install leave the script alone -- :meth:`clear` (``POST
    /api/commands/refresh``) or a restart picks those up. The launcher and API
    share one instance. Requests run on a thread pool, so two guards apply:
    concurrent first requests for one command share a single fetch (each spawns
    the app, which may import a heavy stack), and a fetch that was already
    running when :meth:`clear` refreshed the cache does not store its now-stale
    result.
    """

    def __init__(self) -> None:
        """Create an empty schema cache."""
        self._cache: dict[str, tuple[_Fingerprint | None, dict[str, object]]] = {}
        self._lock = threading.Lock()  # guards the three fields below
        self._fetching: dict[str, threading.Lock] = {}
        self._generation = 0

    def get(self, meta: CommandMeta) -> dict[str, object]:
        """Return the command's JSON Schema, fetching it for a new or changed app."""
        fingerprint = _app_fingerprint(meta)
        with self._lock:
            cached = self._cache.get(meta.key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            fetching = self._fetching.setdefault(meta.key, threading.Lock())
        with fetching:
            with self._lock:
                cached = self._cache.get(meta.key)
                if cached is not None and cached[0] == fingerprint:
                    return cached[1]  # fetched while this request waited its turn
                generation = self._generation
            schema = fetch_schema(meta)
            with self._lock:
                if generation == self._generation:
                    self._cache[meta.key] = (fingerprint, schema)
            return schema

    def clear(self) -> None:
        """Drop all cached schemas (e.g. after an app is upgraded)."""
        with self._lock:
            self._cache.clear()
            self._generation += 1


def fetch_schema(meta: CommandMeta) -> dict[str, object]:
    """Run ``<app> <argv> --schema`` and return the parsed JSON Schema.

    Args:
        meta: The command to introspect.

    Returns:
        The settings model's JSON Schema as a mapping, normalised for form
        rendering.

    Raises:
        SchemaError: If the executable is missing or cannot be run, the process
            fails or times out, or its stdout is not valid JSON.
    """
    executable = shutil.which(meta.app)
    if executable is None:
        msg = f"Command executable {meta.app!r} not found on PATH."
        raise SchemaError(msg)

    argv = [executable, *meta.argv, "--schema"]
    try:
        result = run_tool(argv, timeout=_SCHEMA_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        msg = f"Timed out fetching schema for {meta.key!r} after {_SCHEMA_TIMEOUT_S}s."
        raise SchemaError(msg) from exc
    except OSError as exc:
        # A stale shebang (a moved venv) or a file without the executable bit.
        msg = (
            f"{meta.app!r} could not be run to fetch the schema for {meta.key!r}: {exc}"
        )
        raise SchemaError(msg) from exc
    if result.returncode != 0:
        msg = (
            f"Fetching schema for {meta.key!r} failed "
            f"(exit {result.returncode}): {result.stderr[-_STDERR_TAIL:]}"
        )
        raise SchemaError(msg)

    try:
        schema = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"Schema output for {meta.key!r} was not valid JSON: {exc}"
        raise SchemaError(msg) from exc

    if not isinstance(schema, dict):
        msg = f"Schema for {meta.key!r} was not a JSON object."
        raise SchemaError(msg)
    return cast("dict[str, object]", normalize_for_form(schema))


def normalize_for_form(schema: object) -> object:
    """Rewrite Pydantic's 2020-12 JSON Schema into the shape form renderers want.

    RJSF's field generator speaks Draft-07, so two Pydantic idioms trip it up:

    - ``X | None`` becomes ``anyOf: [X, {"type": "null"}]``, which RJSF renders
      as an ``Option 1 / Option 2`` selector. How that is rewritten depends on
      ``X`` -- see :func:`_collapse_nullable_union`. The rule throughout: an
      untouched field must submit what the model's own default would give.
    - A fixed tuple (e.g. ``tuple[int, int]``) becomes ``prefixItems: [...]``,
      which the renderer cannot handle ("Missing items definition"). It moves to
      the Draft-07 array form ``items: [...]`` so each element gets its own input.

    Only positions that hold schemas are rewritten -- ``properties`` values,
    ``items``, the branches of a union, ``$defs`` entries -- never a property
    *name* or a data keyword such as ``default`` or ``enum``, so a field called
    ``prefixItems`` keeps its name. The root ``$defs`` resolve a ``$ref`` branch,
    which is what tells an optional model from an optional enum.

    The transform is purely for form rendering; the launched CLI still validates
    the real values authoritatively.
    """
    if isinstance(schema, list):
        return [normalize_for_form(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    defs = schema.get("$defs")
    return _normalize(schema, defs if isinstance(defs, dict) else {})


# Keywords whose value is a mapping of name -> schema, or a list of schemas.
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})
_SCHEMA_LISTS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
# Keywords whose value is one schema (``items`` may also be a list of them).
_SCHEMA_ONE = frozenset({"items", "additionalProperties", "not", "contains"})


def _normalize(node: dict[str, object], defs: dict[str, object]) -> dict[str, object]:
    """Normalise one schema object and every schema nested in it."""
    result = _collapse_nullable_union(dict(node), defs)
    array = result.get("type") == "array"
    if array and "prefixItems" in result and "items" not in result:
        result["items"] = result.pop("prefixItems")
    return {key: _normalize_keyword(key, value, defs) for key, value in result.items()}


def _normalize_keyword(key: str, value: object, defs: dict[str, object]) -> object:
    """Normalise a keyword's value if (and only if) it holds schemas."""
    if key in _SCHEMA_MAPS and isinstance(value, dict):
        return {
            name: _normalize_any(sub, defs)
            for name, sub in cast("dict[str, object]", value).items()
        }
    if (key in _SCHEMA_LISTS or key in _SCHEMA_ONE) and isinstance(value, list):
        return [_normalize_any(sub, defs) for sub in cast("list[object]", value)]
    if key in _SCHEMA_ONE:
        return _normalize_any(value, defs)
    return value


def _normalize_any(value: object, defs: dict[str, object]) -> object:
    """``_normalize`` a schema object; leave anything else (``true``) alone."""
    if isinstance(value, dict):
        return _normalize(cast("dict[str, object]", value), defs)
    return value


def _drop_null_default(node: dict[str, object]) -> None:
    """Remove a ``default: null`` the rewritten schema can no longer hold."""
    if "default" in node and node["default"] is None:
        del node["default"]


def _resolve(branch: dict[str, object], defs: dict[str, object]) -> dict[str, object]:
    """``branch``'s ``$ref`` target from the root ``$defs``, else ``branch`` itself."""
    ref = branch.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = defs.get(ref.removeprefix("#/$defs/"))
        if isinstance(target, dict):
            return cast("dict[str, object]", target)
    return branch


def _choices(schema: dict[str, object]) -> list[object] | None:
    """The fixed values of a ``Literal`` / ``Enum`` schema (a ``const`` is one)."""
    enum = schema.get("enum")
    if isinstance(enum, list):
        return cast("list[object]", enum)
    if "const" in schema:
        return [schema["const"]]
    return None


def _collapse_nullable_union(
    node: dict[str, object],
    defs: dict[str, object],
) -> dict[str, object]:
    """Rewrite an ``anyOf``/``oneOf`` that carries a ``{"type": "null"}`` branch.

    Several non-null branches: keep the union, drop the null branch and its now
    invalid null default. One non-null branch ``X``, by what ``X`` is:

    - a plain scalar: inline it as ``type: [X, "null"]``. RJSF renders the
      single input, and AJV accepts both the kept ``None`` default and an empty
      input -- a bare ``type`` with a null default would block submit.
    - a choice (a ``Literal``, or a ``$ref`` to an ``Enum``): inline it as
      ``enum`` + ``type`` with no default. RJSF's select starts empty, the key
      is omitted on submit, and the model's ``None`` applies; a null default
      outside the enum is what made AJV refuse the untouched form.
    - an array of one item schema: inline it without the null default; an
      untouched array is dropped on submit and ``None`` applies.
    - anything else -- a model (``$ref`` or inline object), a fixed tuple, a
      nested union: keep the union, with the null branch titled "None" and the
      null default kept. RJSF otherwise fills in a model's nested defaults, a
      tuple's slots or a union's first branch, and the untouched form submits a
      value the user never chose instead of ``None``.
    """
    for union_key in ("anyOf", "oneOf"):
        variants = node.get(union_key)
        if not isinstance(variants, list):
            continue
        non_null = [
            v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")
        ]
        if len(non_null) == len(variants):
            continue  # no null branch; leave a genuine union alone
        siblings = {k: v for k, v in node.items() if k != union_key}
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            return _collapse_one(
                cast("dict[str, object]", non_null[0]), siblings, union_key, defs
            )
        _drop_null_default(siblings)
        return {**siblings, union_key: non_null}
    return node


def _collapse_one(
    branch: dict[str, object],
    siblings: dict[str, object],
    union_key: str,
    defs: dict[str, object],
) -> dict[str, object]:
    """Rewrite ``X | None`` for its one non-null branch ``X`` (see above)."""
    target = _resolve(branch, defs)
    choices = _choices(target)
    if choices is not None:
        merged = {**siblings, "enum": choices}
        if "type" in target:
            merged["type"] = target["type"]
        _drop_null_default(merged)
        return merged
    branch_type = target.get("type")
    if "$ref" not in branch and branch_type in _NULLABLE_SCALAR_TYPES:
        return {**branch, **siblings, "type": [branch_type, "null"]}
    if (
        "$ref" not in branch
        and branch_type == "array"
        and isinstance(
            branch.get("items"),
            dict,
        )
    ):
        merged = {**branch, **siblings}
        _drop_null_default(merged)
        return merged
    return {**siblings, union_key: [branch, {"type": "null", "title": "None"}]}
