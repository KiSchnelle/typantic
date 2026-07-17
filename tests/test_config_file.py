"""Tests for config-file support: templates, loading, and the CLI behaviour."""

import datetime
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import pytest
import typer
import yaml
from pydantic import BaseModel, Field, computed_field
from typer.testing import CliRunner

from typantic import (
    add_command,
    build_config_template,
    load_config_file,
    write_config_template,
)
from typantic._config_file import unknown_config_keys

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI codes so assertions survive Rich coloring (e.g. CI FORCE_COLOR)."""
    return _ANSI.sub("", text)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Mount(BaseModel):
    source: Path
    dest: Path = Path("/data")


class Cfg(BaseModel):
    name: str  # required scalar
    mounts: list[Mount]  # required list of nested models
    nested: Mount  # required nested model
    count: int = 3
    color: Color = Color.RED
    ratio: tuple[int, int] = (2, 2)
    tags: set[str] = {"a"}
    when: datetime.datetime = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    mode: int = 0o755
    cpus: int = Field(default_factory=lambda data: len(data) + 4)  # validated-data
    note: str | None = None


# ---------------------------------------------------------------------------
# build_config_template
# ---------------------------------------------------------------------------
def test_template_required_scalar_is_placeholder():
    assert build_config_template(Cfg)["name"].startswith("<REQUIRED")


def test_template_required_nested_model_recurses():
    nested = build_config_template(Cfg)["nested"]
    assert nested["source"].startswith("<REQUIRED")
    assert nested["dest"] == "/data"  # nested default shown


def test_template_required_list_of_models_is_example_list():
    mounts = build_config_template(Cfg)["mounts"]
    assert isinstance(mounts, list)
    assert mounts[0]["source"].startswith("<REQUIRED")


def test_template_required_list_of_scalars_is_example_list():
    class Paths(BaseModel):
        files: list[Path]  # required list of scalars

    files = build_config_template(Paths)["files"]
    assert isinstance(files, list)
    assert files[0].startswith("<REQUIRED")
    # The shown shape must reload as a valid list, not a bare scalar string.
    Paths(files=[Path("a.txt")])


def test_template_serialises_enum_tuple_set_datetime():
    t = build_config_template(Cfg)
    assert t["color"] == "red"
    assert t["ratio"] == [2, 2]
    assert t["tags"] == ["a"]
    assert t["when"] == "2020-01-01T00:00:00Z"


def test_template_factory_default_is_sentinel_not_frozen():
    # Factory-computed defaults (host/time-sensitive) must not be frozen into the
    # template; they render as the <DEFAULT: ...> sentinel instead.
    assert build_config_template(Cfg)["cpus"] == "<DEFAULT: computed at runtime>"


def test_load_strips_default_sentinel_so_factory_runs_fresh(tmp_path: Path):
    class Job(BaseModel):
        stamp: str = Field(default_factory=lambda: "computed")

    path = tmp_path / "job.yaml"
    write_config_template(Job, path)
    # The unedited template carries the sentinel...
    assert yaml.safe_load(path.read_text())["stamp"].startswith("<DEFAULT")
    # ...but loading strips it, so the model's factory computes the value.
    assert "stamp" not in load_config_file(path)
    assert Job(**load_config_file(path)).stamp == "computed"


def test_load_strips_nested_default_sentinel(tmp_path: Path):
    class Inner(BaseModel):
        stamp: str = Field(default_factory=lambda: "x")

    class Outer(BaseModel):
        inner: Inner  # required nested model -> recurses
        mounts: list[Inner]  # required list of models -> example list

    path = tmp_path / "outer.json"
    write_config_template(Outer, path)
    loaded = load_config_file(path)
    # Sentinels are stripped recursively, in nested mappings and list elements.
    assert "stamp" not in loaded["inner"]
    assert "stamp" not in loaded["mounts"][0]


def test_template_int_renders_decimal():
    # octal modes have no octal memory; documented decimal rendering.
    assert build_config_template(Cfg)["mode"] == 0o755


def test_template_none_default_preserved():
    assert build_config_template(Cfg)["note"] is None


def test_write_template_yaml_then_round_trips(tmp_path: Path):
    path = tmp_path / "c.yaml"
    write_config_template(Cfg, path)
    assert yaml.safe_load(path.read_text()) == build_config_template(Cfg)


def test_write_template_json_by_suffix(tmp_path: Path):
    path = tmp_path / "c.json"
    write_config_template(Cfg, path)
    assert json.loads(path.read_text()) == build_config_template(Cfg)


# ---------------------------------------------------------------------------
# load_config_file
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_load_yaml(tmp_path: Path, suffix: str):
    path = tmp_path / f"c{suffix}"
    path.write_text("name: x\ncount: 7\n")
    assert load_config_file(path) == {"name": "x", "count": 7}


def test_load_json_uppercase_suffix(tmp_path: Path):
    path = tmp_path / "c.JSON"
    path.write_text('{"name": "x"}')
    assert load_config_file(path) == {"name": "x"}


def test_load_unsupported_suffix_raises_value_error(tmp_path: Path):
    path = tmp_path / "c.txt"
    path.write_text("name: x")
    with pytest.raises(ValueError, match="Unsupported config file type"):
        load_config_file(path)


def test_load_non_mapping_raises_value_error(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="must contain a mapping"):
        load_config_file(path)


def test_load_malformed_raises_value_error(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text("{not valid json")
    with pytest.raises(ValueError, match="could not be parsed"):
        load_config_file(path)


def test_load_empty_yaml_raises_value_error(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("")
    with pytest.raises(ValueError, match="must contain a mapping"):
        load_config_file(path)


# ---------------------------------------------------------------------------
# CLI behaviour (config_file=True)
# ---------------------------------------------------------------------------
class Simple(BaseModel):
    name: str  # required
    count: int = 3
    region: str = "eu"


def _build_app() -> tuple[typer.Typer, dict[str, object]]:
    seen: dict[str, object] = {}

    def run(cfg: Simple) -> None:
        seen.clear()
        seen.update(cfg.model_dump())

    app = typer.Typer()
    add_command(app, Simple, run, name="go", config_file=True)

    @app.command()
    def other() -> None: ...

    return app, seen


def test_generate_config_writes_and_exits_without_running(tmp_path: Path):
    app, seen = _build_app()
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["go", "--generate-config", str(path)])
    assert result.exit_code == 0
    assert not seen  # handler never ran
    assert yaml.safe_load(path.read_text())["name"].startswith("<REQUIRED")


def test_run_from_config_file(tmp_path: Path):
    app, seen = _build_app()
    path = tmp_path / "c.yaml"
    path.write_text("name: alice\ncount: 9\nregion: us\n")
    result = runner.invoke(app, ["go", "--config", str(path)])
    assert result.exit_code == 0
    assert seen == {"name": "alice", "count": 9, "region": "us"}


def test_flag_overrides_config_file(tmp_path: Path):
    app, seen = _build_app()
    path = tmp_path / "c.yaml"
    path.write_text("name: alice\ncount: 9\nregion: us\n")
    result = runner.invoke(app, ["go", "--config", str(path), "--count", "32"])
    assert result.exit_code == 0
    assert seen["count"] == 32  # flag wins
    assert seen["region"] == "us"  # file kept


def test_pure_flags_still_work():
    app, seen = _build_app()
    result = runner.invoke(app, ["go", "--name", "bob"])
    assert result.exit_code == 0
    assert seen == {"name": "bob", "count": 3, "region": "eu"}


def test_unknown_key_in_config_is_rejected(tmp_path: Path):
    app, seen = _build_app()
    path = tmp_path / "c.yaml"
    path.write_text("name: alice\nregionn: us\n")  # regionn: typo for region
    result = runner.invoke(app, ["go", "--config", str(path)])
    assert result.exit_code == 2
    assert "regionn" in _plain(result.output)
    assert not seen  # not run with region silently left at its default


def test_multiple_unknown_keys_are_all_listed(tmp_path: Path):
    app, seen = _build_app()
    path = tmp_path / "c.yaml"
    path.write_text("name: alice\nfoo: 1\nbar: 2\n")
    result = runner.invoke(app, ["go", "--config", str(path)])
    assert result.exit_code == 2
    out = _plain(result.output)
    assert "foo" in out
    assert "bar" in out
    assert not seen


class _Computed(BaseModel):
    factor: int = 2

    @computed_field  # type: ignore[prop-decorator]
    @property
    def doubled(self) -> int:
        return self.factor * 2


def test_computed_field_name_is_allowed_on_reload(tmp_path: Path):
    # A written run-config serialises computed fields; reloading it must not trip
    # the unknown-key check, and the computed value is recomputed, not replayed.
    seen: dict[str, object] = {}

    def run(cfg: _Computed) -> None:
        seen.update(cfg.model_dump())

    app = typer.Typer()
    add_command(app, _Computed, run, name="go", config_file=True)

    @app.command()
    def other() -> None: ...  # force multi-command mode so "go" is a subcommand

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"factor": 5, "doubled": 999}))
    result = runner.invoke(app, ["go", "--config", str(path)])
    assert result.exit_code == 0
    assert seen["factor"] == 5
    assert seen["doubled"] == 10  # recomputed from factor, the file's 999 dropped


class _Inner(BaseModel):
    x: int = 1


class _Outer(BaseModel):
    inner: _Inner = Field(default_factory=_Inner)
    label: str = "a"


class _HasDictField(BaseModel):
    meta: dict[str, int] = Field(default_factory=dict)


def test_unknown_keys_does_not_recurse_into_non_model_dict_field():
    # A dict *value* under a real but non-model field (dict[str, int]) is accepted
    # as-is and not recursed into; its inner keys are left to Pydantic, not flagged.
    assert unknown_config_keys(_HasDictField, {"meta": {"anything": 1}}) == []


def test_unknown_nested_key_is_rejected(tmp_path: Path):
    seen: dict[str, object] = {}

    def run(cfg: _Outer) -> None:
        seen.update(cfg.model_dump())

    app = typer.Typer()
    add_command(app, _Outer, run, name="go", config_file=True)

    @app.command()
    def other() -> None: ...  # force multi-command mode so "go" is a subcommand

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"inner": {"x": 2, "y": 3}}))  # y: typo
    result = runner.invoke(app, ["go", "--config", str(path)])
    assert result.exit_code == 2
    assert "inner.y" in _plain(result.output)
    assert not seen


def test_config_and_generate_config_together_errors(tmp_path: Path):
    app, seen = _build_app()
    cfg = tmp_path / "c.yaml"
    cfg.write_text("name: alice\ncount: 9\nregion: us\n")
    tmpl = tmp_path / "t.yaml"

    result = runner.invoke(
        app,
        ["go", "--config", str(cfg), "--generate-config", str(tmpl)],
    )

    assert result.exit_code == 2  # mutually exclusive, not silently generated
    assert not tmpl.exists()  # generation did not win
    assert not seen  # and the command did not run


def test_missing_required_after_relax_errors_cleanly():
    app, seen = _build_app()
    result = runner.invoke(app, ["go"])
    assert result.exit_code == 2
    assert "name" in result.output.lower()
    assert not seen


def test_config_file_off_means_no_config_option():
    # Default mode: no --config / --generate-config / --schema injected.
    def run(cfg: Simple) -> None: ...

    app = typer.Typer()
    add_command(app, Simple, run, name="go")

    @app.command()
    def other() -> None: ...

    result = runner.invoke(app, ["go", "--help"])
    assert "--config" not in result.output
    assert "--generate-config" not in result.output
    assert "--schema" not in result.output


def test_schema_prints_json_schema_and_exits():
    app, seen = _build_app()
    result = runner.invoke(app, ["go", "--schema"])
    assert result.exit_code == 0
    assert not seen  # handler never ran
    schema = json.loads(result.output)
    assert schema == Simple.model_json_schema()
    assert set(schema["properties"]) == {"name", "count", "region"}


# ---------------------------------------------------------------------------
# CLI behaviour (config_file="only" -- file-only command)
# ---------------------------------------------------------------------------
def _build_file_only_app() -> tuple[typer.Typer, dict[str, object]]:
    seen: dict[str, object] = {}

    def run(cfg: Simple) -> None:
        seen.clear()
        seen.update(cfg.model_dump())

    app = typer.Typer()
    add_command(
        app, Simple, run, name="go", config_file="only", help="Run from a file.",
    )

    @app.command()
    def other() -> None: ...

    return app, seen


def test_file_only_has_no_per_field_flags():
    app, _ = _build_file_only_app()
    out = _plain(runner.invoke(app, ["go", "--help"]).output)
    assert "--config" in out
    assert "--generate-config" in out
    assert "--name" not in out  # no per-field flags are generated
    assert "--count" not in out
    assert "--region" not in out
    assert "Run from a file." in out  # add_command help= is used


def test_file_only_generate_then_run(tmp_path: Path):
    app, seen = _build_file_only_app()
    path = tmp_path / "c.yaml"
    gen = runner.invoke(app, ["go", "--generate-config", str(path)])
    assert gen.exit_code == 0
    assert not seen  # did not run
    assert path.exists()
    path.write_text("name: alice\ncount: 9\nregion: us\n")
    run_res = runner.invoke(app, ["go", "--config", str(path)])
    assert run_res.exit_code == 0
    assert seen == {"name": "alice", "count": 9, "region": "us"}


def test_file_only_without_config_errors():
    app, seen = _build_file_only_app()
    result = runner.invoke(app, ["go"])
    assert result.exit_code == 2
    assert "--config" in _plain(result.output)
    assert not seen


def test_file_only_config_and_generate_together_errors(tmp_path: Path):
    app, seen = _build_file_only_app()
    cfg = tmp_path / "c.yaml"
    cfg.write_text("name: alice\ncount: 9\nregion: us\n")
    tmpl = tmp_path / "t.yaml"

    result = runner.invoke(
        app,
        ["go", "--config", str(cfg), "--generate-config", str(tmpl)],
    )

    assert result.exit_code == 2
    assert not tmpl.exists()  # did not silently generate
    assert not seen


def test_file_only_schema_prints():
    app, seen = _build_file_only_app()
    result = runner.invoke(app, ["go", "--schema"])
    assert result.exit_code == 0
    assert not seen
    assert json.loads(result.output) == Simple.model_json_schema()


def test_template_uses_input_keys_so_it_reloads(tmp_path: Path) -> None:
    # The template is fed straight back through Model(**data), so its keys must
    # be the ones the model accepts -- the alias, not the field name.
    class Cfg(BaseModel):
        threshold: Annotated[float, Field(default=0.5, alias="thr")]

    template = build_config_template(Cfg)
    assert list(template) == ["thr"]
    assert Cfg(**template).threshold == 0.5


def test_template_of_a_self_referential_model_terminates() -> None:
    # Without a cycle guard this recursed until the stack ran out.
    class Node(BaseModel):
        name: str
        children: list["Node"]

    Node.model_rebuild()
    template = build_config_template(Node)
    assert "<REQUIRED" in str(template["name"])
    # The cycle is cut with a placeholder rather than expanded forever.
    assert template["children"] == ["<REQUIRED: children>"]


def test_template_of_a_directly_self_referential_model_terminates() -> None:
    class Loop(BaseModel):
        nested: "Loop | None" = None
        via_required: "Inner"

    class Inner(BaseModel):
        back: "Loop"

    Loop.model_rebuild()
    Inner.model_rebuild()
    template = build_config_template(Loop)
    # Inner expands once, and its way back to Loop stops at the placeholder.
    assert template["via_required"]["back"] == "<REQUIRED: back>"
