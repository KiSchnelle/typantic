import subprocess
import sys
import types
from pathlib import Path

import pytest

from typantic.web import schema as schema_mod
from typantic.web.models import CommandMeta
from typantic.web.schema import (
    SchemaCache,
    SchemaError,
    fetch_schema,
    normalize_for_form,
)

TOY = Path(__file__).parent / "fixtures" / "toy_app.py"


def _meta(argv):
    return CommandMeta(app="python", command="run", argv=argv, title="Run")


# --- fetch_schema: real end-to-end against the toy app (dogfoods --schema) ---


def test_fetch_schema_end_to_end(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: sys.executable)
    result = fetch_schema(_meta((str(TOY), "run")))
    props = result["properties"]
    assert set(props) == {"name", "seed", "workers"}
    seed = props["seed"]
    assert "anyOf" not in seed  # nullable union collapsed
    assert seed["type"] == ["integer", "null"]  # nullability kept so None validates
    assert seed["default"] is None
    assert seed["description"] == "An optional seed."


# --- fetch_schema: error paths ---


def test_fetch_schema_missing_executable(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: None)
    with pytest.raises(SchemaError, match="not found on PATH"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_called_process_error(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")

    def boom(argv, **_):
        raise subprocess.CalledProcessError(1, argv, stderr="bad")

    monkeypatch.setattr(schema_mod.subprocess, "run", boom)
    with pytest.raises(SchemaError, match="failed"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_timeout(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")

    def boom(argv, **_):
        raise subprocess.TimeoutExpired(argv, 1.0)

    monkeypatch.setattr(schema_mod.subprocess, "run", boom)
    with pytest.raises(SchemaError, match="Timed out"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_bad_json(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        schema_mod.subprocess,
        "run",
        lambda *_a, **_k: types.SimpleNamespace(stdout="not json"),
    )
    with pytest.raises(SchemaError, match="not valid JSON"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_not_a_json_object(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        schema_mod.subprocess,
        "run",
        lambda *_a, **_k: types.SimpleNamespace(stdout="[1, 2]"),
    )
    with pytest.raises(SchemaError, match="not a JSON object"):
        fetch_schema(_meta(("x",)))


# --- SchemaCache ---


def test_schema_cache_caches_then_clears(monkeypatch):
    calls = []

    def fake_fetch(meta):
        calls.append(meta.key)
        return {"ok": True}

    monkeypatch.setattr(schema_mod, "fetch_schema", fake_fetch)
    cache = SchemaCache()
    meta = _meta(("x",))
    assert cache.get(meta) == {"ok": True}
    assert cache.get(meta) == {"ok": True}
    assert calls == [meta.key]  # fetched once, then cached
    cache.clear()
    cache.get(meta)
    assert calls == [meta.key, meta.key]  # refetched after clear


# --- normalize_for_form ---


def test_normalize_collapses_nullable_single():
    node = {
        "anyOf": [{"type": "integer"}, {"type": "null"}],
        "default": None,
        "title": "S",
    }
    assert normalize_for_form(node) == {
        "type": ["integer", "null"],
        "default": None,
        "title": "S",
    }


def test_normalize_nullable_scalar_keeps_constraints_and_default():
    # Mirrors a real `float | None = None` field (e.g. a probability threshold):
    # the collapsed field stays nullable so its None default and an empty input
    # both validate, while the numeric bounds and hints survive.
    node = {
        "anyOf": [
            {"type": "number", "minimum": 0.0, "maximum": 1.0},
            {"type": "null"},
        ],
        "default": None,
        "title": "T",
        "description": "d",
    }
    assert normalize_for_form(node) == {
        "type": ["number", "null"],
        "minimum": 0.0,
        "maximum": 1.0,
        "default": None,
        "title": "T",
        "description": "d",
    }


def test_normalize_nullable_ref_drops_null_default():
    # A $ref branch has no scalar `type` to make nullable, so the invalid null
    # default is dropped and the model's own None default applies on omission.
    node = {
        "anyOf": [{"$ref": "#/$defs/E"}, {"type": "null"}],
        "default": None,
        "title": "E",
    }
    assert normalize_for_form(node) == {"$ref": "#/$defs/E", "title": "E"}


def test_normalize_nullable_array_keeps_type_and_drops_null_default():
    # An array branch keeps its string `type: "array"` (the frontend keys array
    # handling off it) and drops the null default instead of becoming a type
    # array.
    node = {
        "anyOf": [
            {"type": "array", "items": {"type": "string"}},
            {"type": "null"},
        ],
        "default": None,
    }
    assert normalize_for_form(node) == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_normalize_multibranch_nullable_drops_null_default():
    node = {
        "anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}],
        "default": None,
    }
    assert normalize_for_form(node) == {
        "anyOf": [{"type": "integer"}, {"type": "string"}],
    }


def test_normalize_keeps_multibranch_union_minus_null():
    node = {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]}
    expected = {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    assert normalize_for_form(node) == expected


def test_normalize_leaves_genuine_union_untouched():
    node = {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    assert normalize_for_form(node) == node


def test_normalize_handles_oneof():
    node = {"oneOf": [{"type": "integer"}, {"type": "null"}]}
    assert normalize_for_form(node) == {"type": ["integer", "null"]}


def test_normalize_prefixitems_to_items():
    node = {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "integer"}]}
    out = normalize_for_form(node)
    assert "prefixItems" not in out
    assert out["items"] == [{"type": "integer"}, {"type": "integer"}]


def test_normalize_prefixitems_kept_when_items_present():
    node = {"prefixItems": [{"type": "integer"}], "items": {"type": "string"}}
    out = normalize_for_form(node)
    assert out["prefixItems"] == [{"type": "integer"}]
    assert out["items"] == {"type": "string"}


def test_normalize_recurses_lists_and_passes_scalars():
    assert normalize_for_form([{"anyOf": [{"type": "integer"}, {"type": "null"}]}]) == [
        {"type": ["integer", "null"]},
    ]
    assert normalize_for_form("scalar") == "scalar"
    assert normalize_for_form(5) == 5


def test_normalize_ignores_non_list_union():
    node = {"anyOf": "weird"}
    assert normalize_for_form(node) == {"anyOf": "weird"}
