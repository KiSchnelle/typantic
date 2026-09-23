import subprocess
import sys
import threading
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


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["x"], returncode, stdout, stderr)


def test_fetch_schema_failing_exit_is_a_schema_error(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        schema_mod,
        "run_tool",
        lambda *_a, **_k: _completed(1, stderr="bad"),
    )
    with pytest.raises(SchemaError, match="failed"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_timeout(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")

    def boom(argv, **_):
        raise subprocess.TimeoutExpired(argv, 1.0)

    monkeypatch.setattr(schema_mod, "run_tool", boom)
    with pytest.raises(SchemaError, match="Timed out"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_bad_json(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        schema_mod,
        "run_tool",
        lambda *_a, **_k: _completed(stdout="not json"),
    )
    with pytest.raises(SchemaError, match="not valid JSON"):
        fetch_schema(_meta(("x",)))


def test_fetch_schema_not_a_json_object(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        schema_mod,
        "run_tool",
        lambda *_a, **_k: _completed(stdout="[1, 2]"),
    )
    with pytest.raises(SchemaError, match="not a JSON object"):
        fetch_schema(_meta(("x",)))


# --- fetch_schema against real child processes ---


def _script(tmp_path, body):
    path = tmp_path / "app.py"
    path.write_text("import os, sys\n" + body)
    return _meta((str(path),))


@pytest.fixture
def python_app(monkeypatch):
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: sys.executable)


def test_an_undecodable_byte_on_stderr_does_not_break_a_good_schema(
    tmp_path,
    python_app,
):
    meta = _script(
        tmp_path,
        "sys.stderr.buffer.write(b'native warning \\xff\\xfe')\n"
        'print(\'{"type": "object"}\')\n',
    )
    assert fetch_schema(meta)["type"] == "object"


def test_an_undecodable_failure_is_reported_not_raised(tmp_path, python_app):
    meta = _script(tmp_path, "sys.stderr.buffer.write(b'\\xff')\nsys.exit(3)\n")
    with pytest.raises(SchemaError, match="exit 3"):
        fetch_schema(meta)


def test_the_child_reads_no_stdin(tmp_path, python_app):
    # A prompt during --schema must not read the operator's terminal.
    meta = _script(
        tmp_path,
        "null, zero = os.stat(os.devnull), os.fstat(0)\n"
        "same = (null.st_dev, null.st_ino) == (zero.st_dev, zero.st_ino)\n"
        'print(\'{"type": "object", "devnull": %s}\' % str(same).lower())\n',
    )
    assert fetch_schema(meta)["devnull"] is True


def test_a_long_failure_message_is_trimmed_to_its_tail(tmp_path, python_app):
    meta = _script(
        tmp_path,
        "sys.stderr.write('x' * 20000 + 'THE-END')\nsys.exit(1)\n",
    )
    with pytest.raises(SchemaError) as exc:
        fetch_schema(meta)
    assert str(exc.value).endswith("THE-END")
    assert len(str(exc.value)) < 5000


def test_an_executable_that_cannot_run_is_a_schema_error(tmp_path, monkeypatch):
    not_executable = tmp_path / "app"
    not_executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(schema_mod.shutil, "which", lambda _: str(not_executable))
    with pytest.raises(SchemaError, match="could not be run"):
        fetch_schema(_meta(("x",)))


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


# --- the schema cache under concurrency ---


def test_a_refresh_during_a_fetch_is_not_undone(monkeypatch):
    cache = SchemaCache()

    def fetch_while_refreshing(_meta):
        cache.clear()  # an app upgrade is refreshed while this fetch is running
        return {"version": "OLD"}

    monkeypatch.setattr(schema_mod, "fetch_schema", fetch_while_refreshing)
    assert cache.get(_meta(("x",))) == {"version": "OLD"}
    # The stale result was not stored: the next request fetches afresh.
    monkeypatch.setattr(schema_mod, "fetch_schema", lambda _m: {"version": "NEW"})
    assert cache.get(_meta(("x",))) == {"version": "NEW"}


def test_concurrent_first_loads_fetch_once(monkeypatch):
    cache = SchemaCache()
    calls = []
    two_in_flight = threading.Event()

    def slow_fetch(_meta):
        calls.append(1)
        if len(calls) == 2:
            two_in_flight.set()
        # Hold the first fetch until a second one starts (which the fix
        # prevents) -- bounded, so the fixed code just proceeds.
        two_in_flight.wait(timeout=0.5)
        return {"type": "object"}

    monkeypatch.setattr(schema_mod, "fetch_schema", slow_fetch)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get(_meta(("x",)))))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [{"type": "object"}, {"type": "object"}]
    assert len(calls) == 1
