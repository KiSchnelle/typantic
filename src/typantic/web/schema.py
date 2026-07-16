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
from typing import cast

from typantic.web.models import CommandMeta

# Importing a heavy settings module can be slow the first time.
_SCHEMA_TIMEOUT_S = 120.0


class SchemaError(RuntimeError):
    """Raised when a command's ``--schema`` invocation fails or is unparseable."""


class SchemaCache:
    """Lazily fetches and caches each command's JSON Schema by command key.

    Schemas are stable for an installed version, so a process-lifetime cache is
    enough; the launcher and API share one instance.
    """

    def __init__(self) -> None:
        """Create an empty schema cache."""
        self._cache: dict[str, dict[str, object]] = {}

    def get(self, meta: CommandMeta) -> dict[str, object]:
        """Return the command's JSON Schema, fetching and caching on first use."""
        cached = self._cache.get(meta.key)
        if cached is None:
            cached = fetch_schema(meta)
            self._cache[meta.key] = cached
        return cached

    def clear(self) -> None:
        """Drop all cached schemas (e.g. after an app is upgraded)."""
        self._cache.clear()


def fetch_schema(meta: CommandMeta) -> dict[str, object]:
    """Run ``<app> <argv> --schema`` and return the parsed JSON Schema.

    Args:
        meta: The command to introspect.

    Returns:
        The settings model's JSON Schema as a mapping, normalised for form
        rendering.

    Raises:
        SchemaError: If the executable is missing, the process fails, times out,
            or its stdout is not valid JSON.
    """
    executable = shutil.which(meta.app)
    if executable is None:
        msg = f"Command executable {meta.app!r} not found on PATH."
        raise SchemaError(msg)

    argv = [executable, *meta.argv, "--schema"]
    try:
        result = subprocess.run(  # noqa: S603 - argv is trusted entry-point metadata
            argv,
            capture_output=True,
            text=True,
            timeout=_SCHEMA_TIMEOUT_S,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"Timed out fetching schema for {meta.key!r} after {_SCHEMA_TIMEOUT_S}s."
        raise SchemaError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = (
            f"Fetching schema for {meta.key!r} failed "
            f"(exit {exc.returncode}): {exc.stderr}"
        )
        raise SchemaError(msg) from exc

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
      ``Option 1 / Option 2`` selector. We collapse a nullable union back to its
      single non-null branch (keeping the field's title/description/default), so
      it renders as one optional field.
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


def _collapse_nullable_union(node: dict[str, object]) -> dict[str, object]:
    """Fold ``anyOf``/``oneOf`` that carries a ``{"type": "null"}`` branch.

    One non-null branch left -> inline it (the field's own title/description/
    default win). Several left -> keep the union but drop the null branch.
    """
    for union_key in ("anyOf", "oneOf"):
        variants = node.get(union_key)
        if not isinstance(variants, list):
            continue
        non_null = [
            v
            for v in variants
            if not (isinstance(v, dict) and v.get("type") == "null")
        ]
        if len(non_null) == len(variants):
            continue  # no null branch; leave a genuine union alone
        siblings = {k: v for k, v in node.items() if k != union_key}
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            return {**non_null[0], **siblings}
        return {**siblings, union_key: non_null}
    return node
