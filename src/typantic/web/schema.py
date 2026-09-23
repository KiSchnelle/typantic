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


class SchemaCache:
    """Lazily fetches and caches each command's JSON Schema by command key.

    Schemas are stable for an installed version, so a process-lifetime cache is
    enough; the launcher and API share one instance. Requests run on a thread
    pool, so two guards apply: concurrent first requests for one command share
    a single fetch (each spawns the app, which may import a heavy stack), and a
    fetch that was already running when :meth:`clear` refreshed the cache does
    not store its now-stale result.
    """

    def __init__(self) -> None:
        """Create an empty schema cache."""
        self._cache: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()  # guards the three fields below
        self._fetching: dict[str, threading.Lock] = {}
        self._generation = 0

    def get(self, meta: CommandMeta) -> dict[str, object]:
        """Return the command's JSON Schema, fetching and caching on first use."""
        with self._lock:
            cached = self._cache.get(meta.key)
            if cached is not None:
                return cached
            fetching = self._fetching.setdefault(meta.key, threading.Lock())
        with fetching:
            with self._lock:
                cached = self._cache.get(meta.key)
                if cached is not None:
                    return cached  # fetched while this request waited its turn
                generation = self._generation
            schema = fetch_schema(meta)
            with self._lock:
                if generation == self._generation:
                    self._cache[meta.key] = schema
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


def normalize_for_form(node: object) -> object:
    """Rewrite Pydantic's 2020-12 JSON Schema into the shape form renderers want.

    RJSF's field generator speaks Draft-07, so two Pydantic idioms trip it up:

    - ``X | None`` becomes ``anyOf: [X, {"type": "null"}]``, which renders as an
      ``Option 1 / Option 2`` selector. We collapse a nullable union to its
      single non-null branch (keeping the field's title/description/default) so
      it renders as one optional field. A nullable scalar keeps its
      nullability as ``type: [X, "null"]`` so the kept ``None`` default stays
      valid; see :func:`_collapse_nullable_union`.
    - A fixed tuple (e.g. ``tuple[int, int]``) becomes ``prefixItems: [...]``,
      which the renderer cannot handle ("Missing items definition"). We move it
      to the Draft-07 array form ``items: [...]`` so each element gets its own
      input.

    The transform is purely for form rendering; the launched CLI still validates
    the real values authoritatively.
    """
    if isinstance(node, list):
        return [normalize_for_form(item) for item in node]
    if not isinstance(node, dict):
        return node

    result: dict[str, object] = dict(node)
    result = _collapse_nullable_union(result)
    if "prefixItems" in result and "items" not in result:
        result["items"] = result.pop("prefixItems")
    return {key: normalize_for_form(value) for key, value in result.items()}


def _drop_null_default(node: dict[str, object]) -> None:
    """Remove a ``default: null`` the collapsed non-null schema can no longer hold.

    Once the ``{"type": "null"}`` branch is gone, a ``null`` default is invalid
    against the remaining type, which makes RJSF/AJV reject the untouched field.
    Dropping it lets the settings model apply its own ``None`` default when the
    field is omitted on submit.
    """
    if "default" in node and node["default"] is None:
        del node["default"]


def _collapse_nullable_union(node: dict[str, object]) -> dict[str, object]:
    """Fold ``anyOf``/``oneOf`` that carries a ``{"type": "null"}`` branch.

    One non-null branch left -> inline it (the field's own title/description/
    default win). Several left -> keep the union but drop the null branch.

    A single scalar branch keeps its nullability as ``type: [X, "null"]``:
    RJSF renders that as the plain non-null input (``getSchemaType`` picks the
    non-null type) while AJV still accepts ``null``, so the field's kept ``None``
    default and an empty input both validate. A bare ``type: number`` carrying
    ``default: null`` would instead fail validation and block submit. Non-scalar
    branches ($ref/enum, array, object) have no scalar ``type`` to make nullable,
    so their invalid ``null`` default is dropped instead.
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
            merged = {**non_null[0], **siblings}
            branch_type = merged.get("type")
            if isinstance(branch_type, str) and branch_type in _NULLABLE_SCALAR_TYPES:
                merged["type"] = [branch_type, "null"]
            else:
                _drop_null_default(merged)
            return merged
        _drop_null_default(siblings)
        return {**siblings, union_key: non_null}
    return node
