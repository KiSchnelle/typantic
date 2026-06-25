"""Tests for config-file support: templates, loading, and the CLI behaviour."""

import datetime
import json
import re
from enum import StrEnum
from pathlib import Path

import pytest
import typer
import yaml
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from typantic import (
    add_command,
    build_config_template,
    load_config_file,
    write_config_template,
)

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


def test_template_serialises_enum_tuple_set_datetime():
    t = build_config_template(Cfg)
    assert t["color"] == "red"
    assert t["ratio"] == [2, 2]
    assert t["tags"] == ["a"]
    assert t["when"] == "2020-01-01T00:00:00Z"


def test_template_one_arg_default_factory_does_not_crash():
    # default_factory taking the validated-data dict must be called with {}.
    assert build_config_template(Cfg)["cpus"] == 4


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


def test_missing_required_after_relax_errors_cleanly():
    app, seen = _build_app()
    result = runner.invoke(app, ["go"])
    assert result.exit_code == 2
    assert "name" in result.output.lower()
    assert not seen


def test_config_file_off_means_no_config_option():
    # Default mode: no --config / --generate-config injected.
    def run(cfg: Simple) -> None: ...

    app = typer.Typer()
    add_command(app, Simple, run, name="go")

    @app.command()
    def other() -> None: ...

    result = runner.invoke(app, ["go", "--help"])
    assert "--config" not in result.output
    assert "--generate-config" not in result.output


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
