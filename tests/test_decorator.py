"""Tests for the pydantic_to_typer decorator."""

import datetime
import decimal
import inspect
import re
import typing
from collections.abc import Callable
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Literal, get_args

import annotated_types
import pytest
import typer
from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
)
from pydantic.alias_generators import to_camel
from typer.testing import CliRunner

from typantic import add_command, pydantic_to_typer
from typantic._decorator import (
    _collect_flat,
    _factory_takes_data,
    _Leaf,
    _numeric_bounds,
    _panel_for_field,
)
from typantic._introspect import extract_base_type

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    """Strip ANSI escape sequences from rendered Rich/Typer output."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _is_directory(p: Path) -> Path:
    if not p.is_dir():
        msg = f"Not a directory: {p}"
        raise ValueError(msg)
    return p


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


def _get_default_threshold() -> float:
    return 0.5


class FullConfig(BaseModel):
    """Model exercising all supported features."""

    folders: Annotated[
        list[
            Annotated[
                Path,
                AfterValidator(Path.resolve),
                AfterValidator(_is_directory),
            ]
        ],
        Field(
            description="Input folders.",
            kw_only=False,
        ),
    ]

    output_dir: Annotated[
        Path,
        AfterValidator(Path.resolve),
        Field(
            description="Output directory.",
            kw_only=True,
        ),
    ]

    seed: Annotated[
        int | None,
        Field(
            default=None,
            description="Random seed.",
            kw_only=True,
        ),
    ]

    threshold: Annotated[
        float,
        Field(
            default_factory=_get_default_threshold,
            description="Confidence threshold.",
            kw_only=True,
        ),
    ]

    dry_run: Annotated[
        bool,
        Field(
            default=False,
            description="Dry-run mode.",
            kw_only=True,
        ),
    ]


class SimpleModel(BaseModel):
    name: Annotated[
        str,
        Field(description="Your name.", kw_only=False),
    ]
    count: Annotated[
        int,
        Field(default=1, description="Repeat count.", kw_only=True),
    ]


# ---------------------------------------------------------------------------
# Helpers: build a Typer app from a model on the fly
# ---------------------------------------------------------------------------


def _make_app(
    model_cls: type[BaseModel],
) -> tuple[typer.Typer, list[BaseModel]]:
    """Return (app, captured_results_list)."""
    app = typer.Typer()
    results: list[BaseModel] = []

    @app.command()
    @pydantic_to_typer(model_cls)
    def cmd(config: BaseModel) -> None:
        results.append(config)

    _ = cmd  # registered via decorator; silence unused-function warnings

    return app, results


def _make_app_config(
    model_cls: type[BaseModel],
) -> tuple[typer.Typer, list[BaseModel]]:
    """Return (app, captured_results_list) for a ``config_file=True`` command."""
    app = typer.Typer()
    results: list[BaseModel] = []

    @app.command()
    @pydantic_to_typer(model_cls, config_file=True)
    def cmd(config: BaseModel) -> None:
        results.append(config)

    _ = cmd

    return app, results


# ---------------------------------------------------------------------------
# Tests: signature rewriting
# ---------------------------------------------------------------------------


class TestSignature:
    @staticmethod
    def _callback(
        app: typer.Typer,
    ) -> Callable[..., object]:
        cb = app.registered_commands[0].callback
        assert cb is not None
        return cb

    def test_parameters_match_model_fields(self) -> None:
        app, _ = _make_app(FullConfig)
        sig = inspect.signature(self._callback(app))
        assert list(sig.parameters) == [
            "folders",
            "output_dir",
            "seed",
            "threshold",
            "dry_run",
        ]

    def test_required_field_has_no_default(self) -> None:
        app, _ = _make_app(FullConfig)
        sig = inspect.signature(self._callback(app))
        assert sig.parameters["folders"].default is inspect.Parameter.empty
        assert sig.parameters["output_dir"].default is inspect.Parameter.empty

    def test_default_value_propagated(self) -> None:
        app, _ = _make_app(SimpleModel)
        sig = inspect.signature(self._callback(app))
        assert sig.parameters["count"].default == 1

    def test_default_factory_passed_as_callable(self) -> None:
        # The factory is passed through to Click as a callable default so it is
        # re-evaluated on every invocation, while still resolving to its value.
        app, _ = _make_app(FullConfig)
        sig = inspect.signature(self._callback(app))
        factory = sig.parameters["threshold"].default
        assert callable(factory)
        assert factory() == 0.5

    def test_none_default_for_optional(self) -> None:
        app, _ = _make_app(FullConfig)
        sig = inspect.signature(self._callback(app))
        assert sig.parameters["seed"].default is None


# ---------------------------------------------------------------------------
# Tests: help output
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_exits_zero(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_argument_shown(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert "Input folders." in _plain(result.output)

    def test_option_shown(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        output = _plain(result.output)
        assert "--output-dir" in output
        assert "Output directory." in output

    def test_optional_field_shown(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        output = _plain(result.output)
        assert "--seed" in output
        assert "Random seed." in output

    def test_default_factory_shown_in_help(self) -> None:
        # The factory is not evaluated for the help sample (a single frozen value
        # would mislead for time/identity factories); a sentinel is shown instead.
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        # Collapse box borders + whitespace: the sample wraps across lines here.
        output = " ".join(_plain(result.output).replace("│", " ").split())
        assert "computed at runtime" in output
        assert "0.5" not in output

    def test_bool_flag_shown(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert "--dry-run" in _plain(result.output)


# ---------------------------------------------------------------------------
# Tests: invocation & validation
# ---------------------------------------------------------------------------


class TestInvocation:
    def test_simple_model(self) -> None:
        app, results = _make_app(SimpleModel)
        result = runner.invoke(app, ["Alice", "--count", "3"])
        assert result.exit_code == 0
        config = results[0]
        assert isinstance(config, SimpleModel)
        assert config.name == "Alice"
        assert config.count == 3

    def test_simple_model_default(self) -> None:
        app, results = _make_app(SimpleModel)
        result = runner.invoke(app, ["Bob"])
        assert result.exit_code == 0
        config = results[0]
        assert isinstance(config, SimpleModel)
        assert config.count == 1

    def test_full_config_all_options(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d1.mkdir()
        (d1 / "file.txt").touch()
        out = tmp_path / "out"
        out.mkdir()

        app, results = _make_app(FullConfig)
        result = runner.invoke(
            app,
            [
                str(d1),
                "--output-dir",
                str(out),
                "--seed",
                "42",
                "--threshold",
                "0.8",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, FullConfig)
        assert len(config.folders) == 1
        assert config.folders[0].is_absolute()
        assert config.seed == 42
        assert config.threshold == 0.8
        assert config.dry_run is True

    def test_seed_none_when_omitted(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d1.mkdir()
        (d1 / "f").touch()
        out = tmp_path / "out"
        out.mkdir()

        app, results = _make_app(FullConfig)
        result = runner.invoke(app, [str(d1), "--output-dir", str(out)])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, FullConfig)
        assert config.seed is None

    def test_multiple_folders(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        out = tmp_path / "out"
        for d in (d1, d2, out):
            d.mkdir()
            (d / "f").touch()

        app, results = _make_app(FullConfig)
        result = runner.invoke(app, [str(d1), str(d2), "--output-dir", str(out)])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, FullConfig)
        assert len(config.folders) == 2


# ---------------------------------------------------------------------------
# Tests: validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_nonexistent_directory_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["/no/such/dir", "--output-dir", str(out)])
        assert result.exit_code != 0
        assert "Not a directory" in _plain(result.output)

    def test_missing_required_option(self, tmp_path: Path) -> None:
        d = tmp_path / "a"
        d.mkdir()
        (d / "f").touch()
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, [str(d)])
        assert result.exit_code != 0

    def test_missing_required_argument(self) -> None:
        app, _ = _make_app(SimpleModel)
        result = runner.invoke(app, [])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Enum & Tuple models
# ---------------------------------------------------------------------------


class Color(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class EnumModel(BaseModel):
    name: Annotated[
        str,
        Field(description="Item name.", kw_only=False),
    ]
    color: Annotated[
        Color,
        Field(default=Color.RED, description="Pick a color.", kw_only=True),
    ]
    priority: Annotated[
        Priority,
        Field(description="Priority level.", kw_only=True),
    ]
    maybe_color: Annotated[
        Color | None,
        Field(default=None, description="Optional color.", kw_only=True),
    ]


class TupleModel(BaseModel):
    name: Annotated[
        str,
        Field(description="Name.", kw_only=False),
    ]
    point: Annotated[
        tuple[float, float],
        Field(description="X Y coordinates.", kw_only=True),
    ]
    bounds: Annotated[
        tuple[int, int, int, int],
        Field(
            default=(0, 0, 100, 100),
            description="Bounding box (x1 y1 x2 y2).",
            kw_only=True,
        ),
    ]


class TupleAnnotatedModel(BaseModel):
    """Tuple with Annotated inner types (validators should be stripped)."""

    coords: Annotated[
        tuple[
            Annotated[float, AfterValidator(lambda v: round(v, 2))],
            Annotated[float, AfterValidator(lambda v: round(v, 2))],
        ],
        Field(description="Rounded coordinates.", kw_only=True),
    ]


# ---------------------------------------------------------------------------
# Tests: Enum
# ---------------------------------------------------------------------------


class TestEnum:
    def test_help_shows_choices(self) -> None:
        app, _ = _make_app(EnumModel)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = _plain(result.output)
        assert "--color" in output
        assert "--priority" in output
        # Typer renders enum choices
        assert "red" in output
        assert "green" in output
        assert "blue" in output

    def test_str_enum_default(self) -> None:
        app, results = _make_app(EnumModel)
        result = runner.invoke(app, ["widget", "--priority", "2"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, EnumModel)
        assert config.color == Color.RED
        assert config.priority == Priority.MEDIUM

    def test_str_enum_explicit(self) -> None:
        app, results = _make_app(EnumModel)
        result = runner.invoke(app, ["widget", "--color", "blue", "--priority", "3"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, EnumModel)
        assert config.color == Color.BLUE
        assert config.priority == Priority.HIGH

    def test_optional_enum_none(self) -> None:
        app, results = _make_app(EnumModel)
        result = runner.invoke(app, ["thing", "--priority", "1"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, EnumModel)
        assert config.maybe_color is None

    def test_optional_enum_set(self) -> None:
        app, results = _make_app(EnumModel)
        result = runner.invoke(
            app, ["thing", "--priority", "1", "--maybe-color", "green"]
        )
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, EnumModel)
        assert config.maybe_color == Color.GREEN

    def test_invalid_enum_value(self) -> None:
        app, _ = _make_app(EnumModel)
        result = runner.invoke(app, ["x", "--priority", "1", "--color", "pink"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tests: Tuple
# ---------------------------------------------------------------------------


class TestTuple:
    def test_help_shows_tuple_option(self) -> None:
        app, _ = _make_app(TupleModel)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = _plain(result.output)
        assert "--point" in output
        assert "--bounds" in output

    def test_tuple_required(self) -> None:
        app, results = _make_app(TupleModel)
        result = runner.invoke(app, ["test", "--point", "1.5", "2.5"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, TupleModel)
        assert config.point == (1.5, 2.5)
        assert config.bounds == (0, 0, 100, 100)  # default

    def test_tuple_override_default(self) -> None:
        app, results = _make_app(TupleModel)
        result = runner.invoke(
            app,
            ["test", "--point", "1", "2", "--bounds", "10", "20", "30", "40"],
        )
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, TupleModel)
        assert config.bounds == (10, 20, 30, 40)

    def test_tuple_wrong_count(self) -> None:
        app, _ = _make_app(TupleModel)
        # --point expects exactly 2 values
        result = runner.invoke(app, ["test", "--point", "1.0"])
        assert result.exit_code != 0

    def test_tuple_with_annotated_inner_types(self) -> None:
        app, results = _make_app(TupleAnnotatedModel)
        result = runner.invoke(app, ["--coords", "3.14159", "2.71828"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, TupleAnnotatedModel)
        # AfterValidator rounds to 2 decimals
        assert config.coords == (3.14, 2.72)


# ---------------------------------------------------------------------------
# Test models: subpanels
# ---------------------------------------------------------------------------


class ComputeMixin(BaseModel):
    cli_panel: ClassVar[str] = "Compute Resources"

    cpus: Annotated[
        int,
        Field(default=4, description="CPU count.", kw_only=True),
    ]
    gpus: Annotated[
        int,
        Field(default=0, description="GPU count.", kw_only=True),
    ]


class UntitledMixin(BaseModel):
    verbose: Annotated[
        bool,
        Field(default=False, description="Verbose output.", kw_only=True),
    ]


class PanelModel(ComputeMixin, UntitledMixin):
    target: Annotated[
        str,
        Field(description="Run target.", kw_only=False),
    ]
    dry_run: Annotated[
        bool,
        Field(default=False, description="Dry run.", kw_only=True),
    ]


class SelfTitledModel(BaseModel):
    cli_panel: ClassVar[str] = "Own Panel"

    speed: Annotated[
        int,
        Field(default=1, description="Speed.", kw_only=True),
    ]


# ---------------------------------------------------------------------------
# Tests: subpanels
# ---------------------------------------------------------------------------


def _make_panel_app(model_cls: type[BaseModel], *, subpanels: bool) -> typer.Typer:
    app = typer.Typer()

    @app.command()
    @pydantic_to_typer(model_cls, subpanels=subpanels)
    def cmd(config: BaseModel) -> None: ...

    _ = cmd
    return app


def _param_meta(app: typer.Typer, name: str) -> typer.models.ParameterInfo:
    cb = app.registered_commands[0].callback
    assert cb is not None
    meta = get_args(cb.__annotations__[name])[1]
    assert isinstance(meta, typer.models.ParameterInfo)
    return meta


class TestSubpanels:
    def test_off_by_default(self) -> None:
        app, _ = _make_app(PanelModel)
        meta = _param_meta(app, "cpus")
        assert meta.rich_help_panel is None

    def test_panel_from_defining_mixin(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        assert _param_meta(app, "cpus").rich_help_panel == "Compute Resources"
        assert _param_meta(app, "gpus").rich_help_panel == "Compute Resources"

    def test_mixin_without_cli_panel_stays_default(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        assert _param_meta(app, "verbose").rich_help_panel is None

    def test_model_own_field_without_cli_panel_stays_default(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        assert _param_meta(app, "dry_run").rich_help_panel is None

    def test_model_with_own_cli_panel_groups_its_fields(self) -> None:
        app = _make_panel_app(SelfTitledModel, subpanels=True)
        assert _param_meta(app, "speed").rich_help_panel == "Own Panel"

    def test_argument_never_panelled(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        assert _param_meta(app, "target").rich_help_panel is None

    def test_panel_title_rendered_in_help(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Compute Resources" in _plain(result.output)

    def test_panel_title_absent_when_disabled(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=False)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Compute Resources" not in _plain(result.output)

    def test_cli_panel_not_a_cli_option(self) -> None:
        app = _make_panel_app(PanelModel, subpanels=True)
        result = runner.invoke(app, ["--help"])
        assert "--cli-panel" not in _plain(result.output)


# ---------------------------------------------------------------------------
# Tests: None defaults in help
# ---------------------------------------------------------------------------


class TestNoneDefaultHelp:
    def test_none_default_rendered(self) -> None:
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "(None)" in _plain(result.output)

    def test_regular_default_still_rendered(self) -> None:
        # A plain (non-factory, non-None) default still renders its value.
        app, _ = _make_app(SimpleModel)
        result = runner.invoke(app, ["--help"])
        assert "[default: 1]" in _plain(result.output)

    def test_none_default_still_passes_none(self) -> None:
        app, results = _make_app(SimpleModel)
        result = runner.invoke(app, ["Alice"])
        assert result.exit_code == 0
        config = results[0]
        assert isinstance(config, SimpleModel)
        assert config.count == 1


# ---------------------------------------------------------------------------
# Tests: Literal choices
# ---------------------------------------------------------------------------


class LiteralModel(BaseModel):
    mode: Annotated[
        Literal["fast", "slow"],
        Field(default="fast", description="Run mode.", kw_only=True),
    ]


class TestLiteral:
    def test_help_shows_choices(self) -> None:
        app, _ = _make_app(LiteralModel)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "fast" in output
        assert "slow" in output

    def test_valid_choice_accepted(self) -> None:
        app, results = _make_app(LiteralModel)
        result = runner.invoke(app, ["--mode", "slow"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, LiteralModel)
        assert config.mode == "slow"

    def test_invalid_choice_rejected(self) -> None:
        app, _ = _make_app(LiteralModel)
        result = runner.invoke(app, ["--mode", "bogus"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tests: lazy default_factory
# ---------------------------------------------------------------------------


_counter = {"n": 0}


def _increment() -> int:
    _counter["n"] += 1
    return _counter["n"]


class CounterModel(BaseModel):
    ticket: Annotated[
        int,
        Field(default_factory=_increment, description="Ticket number.", kw_only=True),
    ]


class TestLazyFactory:
    def test_factory_runs_per_invocation(self) -> None:
        _counter["n"] = 0
        app, results = _make_app(CounterModel)
        runner.invoke(app, [])
        runner.invoke(app, [])
        first, second = results[0], results[1]
        assert isinstance(first, CounterModel)
        assert isinstance(second, CounterModel)
        assert first.ticket != second.ticket

    def test_explicit_value_overrides_factory(self) -> None:
        app, results = _make_app(CounterModel)
        result = runner.invoke(app, ["--ticket", "99"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, CounterModel)
        assert config.ticket == 99

    def test_sample_value_shown_in_help(self) -> None:
        app, _ = _make_app(CounterModel)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "default" in output.lower()


# ---------------------------------------------------------------------------
# Tests: SecretStr
# ---------------------------------------------------------------------------


class SecretModel(BaseModel):
    name: Annotated[
        str,
        Field(description="Name.", kw_only=False),
    ]
    token: Annotated[
        SecretStr,
        Field(description="API token.", kw_only=True),
    ]


class TestSecret:
    def test_secret_value_wrapped(self) -> None:
        app, results = _make_app(SecretModel)
        result = runner.invoke(app, ["alice", "--token", "hunter2"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, SecretModel)
        assert isinstance(config.token, SecretStr)
        assert config.token.get_secret_value() == "hunter2"

    def test_secret_prompts_when_omitted(self) -> None:
        app, results = _make_app(SecretModel)
        result = runner.invoke(app, ["alice"], input="prompted\n")
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, SecretModel)
        assert config.token.get_secret_value() == "prompted"

    def test_secret_option_listed_in_help(self) -> None:
        app, _ = _make_app(SecretModel)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "--token" in output


# ---------------------------------------------------------------------------
# Tests: CLI hints (short flags, custom names, envvars)
# ---------------------------------------------------------------------------


class HintModel(BaseModel):
    verbose: Annotated[
        bool,
        Field(
            default=False,
            description="Verbose output.",
            kw_only=True,
            json_schema_extra={"cli_short": "-v"},
        ),
    ]
    output: Annotated[
        str,
        Field(
            default="out",
            description="Output path.",
            kw_only=True,
            json_schema_extra={"cli_name": "--dest"},
        ),
    ]
    api_key: Annotated[
        str,
        Field(
            default="",
            description="API key.",
            kw_only=True,
            json_schema_extra={"cli_envvar": "TYPANTIC_API_KEY"},
        ),
    ]


class TestCliHints:
    def test_short_flag(self) -> None:
        app, results = _make_app(HintModel)
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, HintModel)
        assert config.verbose is True

    def test_long_flag_still_works_with_short(self) -> None:
        app, results = _make_app(HintModel)
        result = runner.invoke(app, ["--verbose"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, HintModel)
        assert config.verbose is True

    def test_custom_name(self) -> None:
        app, results = _make_app(HintModel)
        result = runner.invoke(app, ["--dest", "build"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, HintModel)
        assert config.output == "build"

    def test_custom_name_replaces_default_flag(self) -> None:
        app, _ = _make_app(HintModel)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "--dest" in output
        assert "--output" not in output

    def test_envvar(self) -> None:
        app, results = _make_app(HintModel)
        result = runner.invoke(app, [], env={"TYPANTIC_API_KEY": "from-env"})
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, HintModel)
        assert config.api_key == "from-env"


# ---------------------------------------------------------------------------
# Tests: numeric constraints
# ---------------------------------------------------------------------------


class BoundedModel(BaseModel):
    level: Annotated[
        int,
        Field(default=5, ge=0, le=10, description="Level.", kw_only=True),
    ]


class OptionalBoundedModel(BaseModel):
    level: Annotated[
        int | None,
        Field(default=None, ge=0, le=10, description="Level.", kw_only=True),
    ]


class TestNumericBounds:
    def test_in_range_accepted(self) -> None:
        app, results = _make_app(BoundedModel)
        result = runner.invoke(app, ["--level", "7"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, BoundedModel)
        assert config.level == 7

    def test_above_max_rejected(self) -> None:
        app, _ = _make_app(BoundedModel)
        result = runner.invoke(app, ["--level", "20"])
        assert result.exit_code != 0

    def test_below_min_rejected(self) -> None:
        app, _ = _make_app(BoundedModel)
        result = runner.invoke(app, ["--level", "-1"])
        assert result.exit_code != 0

    def test_range_shown_in_help(self) -> None:
        app, _ = _make_app(BoundedModel)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "10" in output

    def test_optional_bounds_enforced_by_typer(self) -> None:
        # Optional[int] leaves base type as int | None; bounds must still apply.
        app, _ = _make_app(OptionalBoundedModel)
        assert runner.invoke(app, ["--level", "20"]).exit_code != 0

    def test_optional_range_shown_in_help(self) -> None:
        app, _ = _make_app(OptionalBoundedModel)
        output = " ".join(_plain(runner.invoke(app, ["--help"]).output).split())
        assert "x<=10" in output or "0<=x<=10" in output


# ---------------------------------------------------------------------------
# Test models: nested
# ---------------------------------------------------------------------------


class Database(BaseModel):
    host: Annotated[
        str,
        Field(default="localhost", description="DB host.", kw_only=True),
    ]
    port: Annotated[
        int,
        Field(default=5432, ge=1, le=65535, description="DB port.", kw_only=True),
    ]


def _nonempty(v: str) -> str:
    if not v:
        msg = "must not be empty"
        raise ValueError(msg)
    return v


class NestedConfig(BaseModel):
    name: Annotated[
        str,
        Field(description="App name.", kw_only=False),
    ]
    db: Database
    label: Annotated[
        str,
        AfterValidator(_nonempty),
        Field(default="x", description="Label.", kw_only=True),
    ]


class TestNested:
    def test_help_shows_prefixed_options(self) -> None:
        app, _ = _make_app(NestedConfig)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "--db-host" in output
        assert "--db-port" in output

    def test_nested_defaults(self) -> None:
        app, results = _make_app(NestedConfig)
        result = runner.invoke(app, ["myapp"])
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, NestedConfig)
        assert config.db.host == "localhost"
        assert config.db.port == 5432

    def test_nested_override(self) -> None:
        app, results = _make_app(NestedConfig)
        result = runner.invoke(
            app, ["myapp", "--db-host", "remote", "--db-port", "9000"]
        )
        assert result.exit_code == 0, result.output
        config = results[0]
        assert isinstance(config, NestedConfig)
        assert config.db.host == "remote"
        assert config.db.port == 9000

    def test_nested_numeric_bound_enforced(self) -> None:
        app, _ = _make_app(NestedConfig)
        result = runner.invoke(app, ["myapp", "--db-port", "99999"])
        assert result.exit_code != 0

    def test_outer_validator_still_runs(self) -> None:
        app, _ = _make_app(NestedConfig)
        result = runner.invoke(app, ["myapp", "--label", ""])
        assert result.exit_code != 0
        assert "must not be empty" in _plain(result.output)


# ---------------------------------------------------------------------------
# Tests: flattened name collisions
# ---------------------------------------------------------------------------


class _CollisionInner(BaseModel):
    host: Annotated[str, Field(default="h", kw_only=True)]


class CollisionConfig(BaseModel):
    db: _CollisionInner  # db.host -> db_host
    db_host: Annotated[str, Field(default="s", kw_only=True)]  # sibling clash


class TestNameCollision:
    def test_collision_raises_named_diagnostic(self) -> None:
        with pytest.raises(ValueError, match="CLI name collision") as excinfo:
            _make_app(CollisionConfig)
        message = str(excinfo.value)
        # Both colliding fields and the resulting flag are named.
        assert "db.host" in message
        assert "db_host" in message
        assert "--db-host" in message


# ---------------------------------------------------------------------------
# Tests: add_command helper
# ---------------------------------------------------------------------------


class TestAddCommand:
    def test_registers_and_runs(self) -> None:
        app = typer.Typer()
        captured: list[BaseModel] = []

        def handler(config: BaseModel) -> None:
            captured.append(config)

        add_command(app, SimpleModel, handler)
        result = runner.invoke(app, ["Alice", "--count", "2"])
        assert result.exit_code == 0, result.output
        config = captured[0]
        assert isinstance(config, SimpleModel)
        assert config.name == "Alice"
        assert config.count == 2

    def test_custom_name(self) -> None:
        app = typer.Typer()
        add_command(app, SimpleModel, lambda _: None, name="greet")
        registered = app.registered_commands[0]
        assert registered.name == "greet"

    def test_subpanels_forwarded(self) -> None:
        app = typer.Typer()
        add_command(app, PanelModel, lambda _: None, subpanels=True)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "Compute Resources" in output


# ---------------------------------------------------------------------------
# Internal helper edge branches
# ---------------------------------------------------------------------------
def test_panel_for_field_returns_none_for_unknown_field() -> None:
    class M(BaseModel):
        x: int = 1

    # No class in the MRO defines "nope", so the resolver falls through to None.
    assert _panel_for_field(M, "nope") is None


def test_numeric_bounds_reads_ge_le_from_an_interval() -> None:
    class M(BaseModel):
        x: Annotated[int, annotated_types.Interval(ge=0, le=10)]

    # Pydantic keeps the grouped Interval in metadata (rather than splitting it
    # into separate Ge/Le), so both bounds come from the one constraint.
    assert _numeric_bounds(M.model_fields["x"]) == (0.0, 10.0)


def test_numeric_bounds_interval_with_open_lower_side() -> None:
    class M(BaseModel):
        x: Annotated[int, annotated_types.Interval(gt=None, ge=None, lt=None, le=5)]

    # An Interval with ge unset exercises the "only le" side of the branch.
    assert _numeric_bounds(M.model_fields["x"]) == (None, 5.0)


def test_numeric_bounds_interval_with_open_upper_side() -> None:
    class M(BaseModel):
        x: Annotated[int, annotated_types.Interval(gt=None, ge=3, lt=None, le=None)]

    # ...and one with le unset exercises the "only ge" side.
    assert _numeric_bounds(M.model_fields["x"]) == (3.0, None)


def test_numeric_bounds_ignores_non_ge_le_constraints() -> None:
    class M(BaseModel):
        x: Annotated[int, annotated_types.Gt(0)]

    # A constraint that is neither Ge, Le, nor Interval (here exclusive Gt) is
    # skipped -- Typer has no exclusive min/max, so it maps to no bound.
    assert _numeric_bounds(M.model_fields["x"]) == (None, None)


def test_collect_flat_skips_cli_names_absent_from_kwargs() -> None:
    mapping = [_Leaf("a", ("a",), ("a",), ()), _Leaf("b", ("b",), ("b",), ())]
    # "b" is not among the supplied kwargs, so it is skipped, not defaulted in.
    assert _collect_flat(mapping, {"a": 1}, set()) == {"a": 1}


def test_collect_flat_keys_values_by_input_key_not_field_name() -> None:
    # The flag follows the field name; the value is re-nested under the alias, so
    # an aliased model actually receives it.
    mapping = [_Leaf("threshold", ("thr",), ("threshold",), ())]
    assert _collect_flat(mapping, {"threshold": 0.9}, set()) == {"thr": 0.9}


def test_collect_flat_drops_deferred_none_so_pydantic_runs_the_factory() -> None:
    # A validated-data default_factory cannot run at the Click layer, so its
    # untouched None is dropped rather than passed through as a real value.
    mapping = [_Leaf("b", ("b",), ("b",), ())]
    assert _collect_flat(mapping, {"b": None}, {"b"}) == {}
    # An explicitly-supplied value still wins.
    assert _collect_flat(mapping, {"b": 7}, {"b"}) == {"b": 7}


def test_extract_base_type_passes_through_parameterless_generics() -> None:
    # A bare legacy generic (origin set, no args) is returned unchanged rather
    # than re-wrapped.
    assert extract_base_type(typing.List) is typing.List  # noqa: UP006
    assert extract_base_type(typing.Tuple) is typing.Tuple  # noqa: UP006


# ---------------------------------------------------------------------------
# Tests: field aliases
#
# Pydantic populates by alias, not by field name. Keying the model kwargs off
# the field name made an aliased field either unrunnable (required) or silently
# defaulted (optional) -- the CLI accepted the flag and threw the value away.
# ---------------------------------------------------------------------------


class TestAliases:
    def test_defaulted_alias_receives_the_passed_value(self) -> None:
        class Cfg(BaseModel):
            threshold: Annotated[
                float,
                Field(default=0.5, alias="thr", kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--threshold", "0.9"])
        assert result.exit_code == 0
        # Not 0.5: the value must not be silently discarded.
        assert seen[0].threshold == 0.9

    def test_required_alias_is_runnable(self) -> None:
        class Cfg(BaseModel):
            real: Annotated[int, Field(alias="aka", kw_only=True)]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--real", "5"])
        assert result.exit_code == 0
        assert seen[0].real == 5

    def test_validation_alias_is_used_over_the_serialisation_alias(self) -> None:
        class Cfg(BaseModel):
            out: Annotated[
                str,
                Field(
                    default="d",
                    validation_alias="v_in",
                    serialization_alias="s_out",
                    kw_only=True,
                ),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--out", "x"])
        assert result.exit_code == 0
        assert seen[0].out == "x"

    def test_alias_generator_maps_every_field(self) -> None:
        class Cfg(BaseModel):
            model_config = ConfigDict(alias_generator=to_camel)

            output_dir: Annotated[str, Field(default="d", kw_only=True)]
            max_workers: Annotated[int, Field(default=1, kw_only=True)]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--output-dir", "x", "--max-workers", "8"])
        assert result.exit_code == 0
        assert (seen[0].output_dir, seen[0].max_workers) == ("x", 8)

    def test_populate_by_name_keeps_the_field_name(self) -> None:
        class Cfg(BaseModel):
            model_config = ConfigDict(populate_by_name=True)

            threshold: Annotated[
                float,
                Field(default=0.5, alias="thr", kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--threshold", "0.9"])
        assert result.exit_code == 0
        assert seen[0].threshold == 0.9

    def test_nested_aliased_field_receives_the_passed_value(self) -> None:
        class Inner(BaseModel):
            host: Annotated[str, Field(default="localhost", alias="h", kw_only=True)]

        class Cfg(BaseModel):
            db: Annotated[Inner, Field(default_factory=Inner, alias="database")]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--db-host", "prod"])
        assert result.exit_code == 0
        assert seen[0].db.host == "prod"

    def test_alias_choices_is_rejected_at_decoration_with_a_clear_error(self) -> None:
        class Cfg(BaseModel):
            x: Annotated[
                int,
                Field(default=1, validation_alias=AliasChoices("a", "b"), kw_only=True),
            ]

        with pytest.raises(ValueError, match="AliasChoices"):
            pydantic_to_typer(Cfg)(lambda config: config)

    def test_alias_choices_is_allowed_when_populate_by_name(self) -> None:
        # The field name is a valid input key here, so the alias is never needed.
        class Cfg(BaseModel):
            model_config = ConfigDict(populate_by_name=True)

            x: Annotated[
                int,
                Field(default=1, validation_alias=AliasChoices("a", "b"), kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--x", "5"])
        assert result.exit_code == 0
        assert seen[0].x == 5


# ---------------------------------------------------------------------------
# Tests: nested-model defaults
# ---------------------------------------------------------------------------


class _Database(BaseModel):
    host: Annotated[str, Field(default="localhost", kw_only=True)]
    port: Annotated[int, Field(default=5432, kw_only=True)]


class TestNestedDefaults:
    def test_outer_default_instance_wins_over_the_inner_class_defaults(self) -> None:
        class Cfg(BaseModel):
            db: Annotated[
                _Database,
                Field(default=_Database(host="prod", port=9999), kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        # Not localhost:5432 -- the field's own default must not be discarded.
        assert (seen[0].db.host, seen[0].db.port) == ("prod", 9999)

    def test_help_advertises_the_outer_default(self) -> None:
        class Cfg(BaseModel):
            db: Annotated[
                _Database,
                Field(default=_Database(host="prod", port=9999), kw_only=True),
            ]

        app, _ = _make_app(Cfg)
        output = _plain(runner.invoke(app, ["--help"]).output)
        assert "prod" in output
        assert "localhost" not in output

    def test_overriding_one_leaf_keeps_the_others_from_the_outer_default(self) -> None:
        class Cfg(BaseModel):
            db: Annotated[
                _Database,
                Field(default=_Database(host="prod", port=9999), kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--db-host", "staging"])
        assert result.exit_code == 0
        assert (seen[0].db.host, seen[0].db.port) == ("staging", 9999)

    def test_a_required_nested_field_still_uses_the_inner_defaults(self) -> None:
        class Cfg(BaseModel):
            db: _Database

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert (seen[0].db.host, seen[0].db.port) == ("localhost", 5432)

    def test_default_factory_instance_also_seeds_the_leaves(self) -> None:
        class Cfg(BaseModel):
            db: Annotated[
                _Database,
                Field(default_factory=lambda: _Database(host="made"), kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert seen[0].db.host == "made"


# ---------------------------------------------------------------------------
# Tests: secrets, numeric bounds, flag collisions, --config errors
# ---------------------------------------------------------------------------


class TestSecrets:
    def test_secret_bytes_is_settable(self) -> None:
        class Cfg(BaseModel):
            token: Annotated[
                SecretBytes,
                Field(default=SecretBytes(b"x"), kw_only=True),
            ]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--token", "hunter2"])
        assert result.exit_code == 0
        assert seen[0].token.get_secret_value() == b"hunter2"

    def test_optional_secret_is_settable(self) -> None:
        class Cfg(BaseModel):
            token: Annotated[SecretStr | None, Field(default=None, kw_only=True)]

        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--token", "hunter2"])
        assert result.exit_code == 0
        assert seen[0].token is not None
        assert seen[0].token.get_secret_value() == "hunter2"

    def test_optional_secret_defaults_to_none(self) -> None:
        class Cfg(BaseModel):
            token: Annotated[SecretStr | None, Field(default=None, kw_only=True)]

        app, seen = _make_app(Cfg)
        assert runner.invoke(app, []).exit_code == 0
        assert seen[0].token is None

    def test_secret_value_is_not_echoed_in_help(self) -> None:
        class Cfg(BaseModel):
            token: Annotated[
                SecretStr,
                Field(default=SecretStr("swordfish"), kw_only=True),
            ]

        app, _ = _make_app(Cfg)
        assert "swordfish" not in _plain(runner.invoke(app, ["--help"]).output)


class TestNumericBoundPrecision:
    def test_int_bounds_keep_full_precision(self) -> None:
        # Above 2**53 a float cannot hold the bound exactly, and rounding it the
        # wrong way rejects the only value Pydantic would accept.
        big = 2**53 + 1

        class Cfg(BaseModel):
            n: Annotated[int, Field(default=big, ge=big, le=big, kw_only=True)]

        assert _numeric_bounds(Cfg.model_fields["n"]) == (big, big)
        app, seen = _make_app(Cfg)
        result = runner.invoke(app, ["--n", str(big)])
        assert result.exit_code == 0
        assert seen[0].n == big

    def test_decimal_bounds_are_passed_to_click(self) -> None:
        class Cfg(BaseModel):
            d: Annotated[
                decimal.Decimal,
                Field(default=decimal.Decimal("1.5"), ge=decimal.Decimal("1.0")),
            ]

        assert _numeric_bounds(Cfg.model_fields["d"]) == (1.0, None)

    def test_non_numeric_bound_is_ignored(self) -> None:
        # A Ge on a non-numeric (e.g. a date) has no Click min/max equivalent.
        class Cfg(BaseModel):
            when: Annotated[
                datetime.date,
                Field(default=datetime.date(2026, 1, 1), ge=datetime.date(2020, 1, 1)),
            ]

        assert _numeric_bounds(Cfg.model_fields["when"]) == (None, None)


class TestFlagCollisions:
    def test_two_fields_claiming_one_flag_is_rejected(self) -> None:
        class Cfg(BaseModel):
            first: Annotated[
                str,
                Field(default="a", kw_only=True, json_schema_extra={"cli_name": "--o"}),
            ]
            second: Annotated[
                str,
                Field(default="b", kw_only=True, json_schema_extra={"cli_name": "--o"}),
            ]

        with pytest.raises(ValueError, match="CLI flag collision"):
            pydantic_to_typer(Cfg)(lambda config: config)

    def test_two_fields_sharing_a_short_flag_is_rejected(self) -> None:
        class Cfg(BaseModel):
            alpha: Annotated[
                str,
                Field(default="a", kw_only=True, json_schema_extra={"cli_short": "-x"}),
            ]
            beta: Annotated[
                str,
                Field(default="b", kw_only=True, json_schema_extra={"cli_short": "-x"}),
            ]

        with pytest.raises(ValueError, match="CLI flag collision"):
            pydantic_to_typer(Cfg)(lambda config: config)

    def test_distinct_flags_are_accepted(self) -> None:
        class Cfg(BaseModel):
            alpha: Annotated[
                str,
                Field(default="a", kw_only=True, json_schema_extra={"cli_short": "-a"}),
            ]
            beta: Annotated[
                str,
                Field(default="b", kw_only=True, json_schema_extra={"cli_short": "-b"}),
            ]

        app, seen = _make_app(Cfg)
        assert runner.invoke(app, ["-a", "x", "-b", "y"]).exit_code == 0
        assert (seen[0].alpha, seen[0].beta) == ("x", "y")

    def test_an_argument_claims_no_flag(self) -> None:
        # Two positional arguments share no flag, so they must not collide.
        class Cfg(BaseModel):
            src: Annotated[str, Field(kw_only=False)]
            dst: Annotated[str, Field(kw_only=False)]

        app, seen = _make_app(Cfg)
        assert runner.invoke(app, ["a", "b"]).exit_code == 0
        assert (seen[0].src, seen[0].dst) == ("a", "b")


class TestConfigFileErrors:
    def test_missing_config_file_is_a_parameter_error(self, tmp_path: Path) -> None:
        # Not a raw FileNotFoundError traceback.
        app, _ = _make_app_config(SimpleModel)
        result = runner.invoke(app, ["--config", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 2
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Invalid value" in _plain(result.output)

    def test_unsupported_suffix_is_a_parameter_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "cfg.txt"
        bad.write_text("name: x")
        app, _ = _make_app_config(SimpleModel)
        result = runner.invoke(app, ["--config", str(bad)])
        assert result.exit_code == 2
        assert "Unsupported config file type" in _plain(result.output)


def test_bool_constraint_is_not_a_numeric_bound() -> None:
    # bool is an int subclass; a bool constraint is not a Click min/max.
    class M(BaseModel):
        flag: Annotated[bool, Field(default=False, ge=False)]

    assert _numeric_bounds(M.model_fields["flag"]) == (None, None)


def test_factory_without_an_introspectable_signature_is_not_data_taking() -> None:
    # A C builtin (dict) has no signature; it must not be mistaken for a
    # validated-data factory and have its value dropped.
    assert _factory_takes_data(dict) is False

    class M(BaseModel):
        tags: Annotated[list[str], Field(default_factory=list, kw_only=True)]

    app, seen = _make_app(M)
    assert runner.invoke(app, []).exit_code == 0
    assert seen[0].tags == []


def test_one_arg_callable_with_a_default_is_not_data_taking() -> None:
    # Pydantic requires the single parameter to have no default, so a callable
    # like this is an ordinary zero-arg factory as far as the CLI is concerned.
    assert _factory_takes_data(lambda data=None: data) is False



class TestValidatedDataFactory:
    # Pydantic 2.10+ lets a default_factory take the validated data. Click calls
    # a default with no arguments, so handing it such a factory raised TypeError
    # on every invocation.

    @staticmethod
    def _model() -> type[BaseModel]:
        class Cfg(BaseModel):
            a: Annotated[int, Field(default=2, kw_only=True)]
            b: Annotated[
                int,
                Field(default_factory=lambda data: data["a"] * 10, kw_only=True),
            ]

        return Cfg

    def test_factory_runs_inside_pydantic(self) -> None:
        app, seen = _make_app(self._model())
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert seen[0].b == 20

    def test_factory_sees_other_flags(self) -> None:
        app, seen = _make_app(self._model())
        assert runner.invoke(app, ["--a", "3"]).exit_code == 0
        assert seen[0].b == 30

    def test_an_explicit_value_still_wins(self) -> None:
        app, seen = _make_app(self._model())
        assert runner.invoke(app, ["--b", "7"]).exit_code == 0
        assert seen[0].b == 7

    def test_help_shows_the_runtime_sentinel(self) -> None:
        app, _ = _make_app(self._model())
        output = " ".join(_plain(runner.invoke(app, ["--help"]).output).split())
        assert "computed at runtime" in output


def test_float_bounds_are_passed_through() -> None:
    class Cfg(BaseModel):
        ratio: Annotated[float, Field(default=0.5, ge=0.0, le=1.0, kw_only=True)]

    assert _numeric_bounds(Cfg.model_fields["ratio"]) == (0.0, 1.0)


def test_nested_default_seeds_a_deeper_level() -> None:
    # The parent instance's value is threaded down through every level, so a
    # two-deep nested default is honoured too.
    class Leaf(BaseModel):
        v: Annotated[str, Field(default="leaf-default", kw_only=True)]

    class Mid(BaseModel):
        leaf: Annotated[Leaf, Field(default=Leaf(v="mid-says"), kw_only=True)]

    class Cfg(BaseModel):
        mid: Annotated[Mid, Field(default=Mid(leaf=Leaf(v="top-says")), kw_only=True)]

    app, seen = _make_app(Cfg)
    assert runner.invoke(app, []).exit_code == 0
    # The outermost default wins over both inner ones.
    assert seen[0].mid.leaf.v == "top-says"


def test_nested_factory_returning_a_non_model_falls_back() -> None:
    # A factory that does not produce an instance leaves the class's own field
    # defaults in place rather than seeding from a non-model.
    class Inner(BaseModel):
        x: Annotated[int, Field(default=1, kw_only=True)]

    class Cfg(BaseModel):
        inner: Annotated[Inner, Field(default_factory=dict, kw_only=True)]

    app, seen = _make_app(Cfg)
    assert runner.invoke(app, []).exit_code == 0
    assert seen[0].inner.x == 1


def test_set_and_variadic_tuple_map_to_a_repeatable_flag() -> None:
    # Typer renders only list among the collections, so these map to list[X] and
    # Pydantic coerces the result back to the declared type.
    assert extract_base_type(set[str]) == list[str]
    assert extract_base_type(frozenset[int]) == list[int]
    assert extract_base_type(tuple[str, ...]) == list[str]
    # A fixed tuple keeps its shape (Typer renders it as a multi-value option).
    assert extract_base_type(tuple[int, int]) == tuple[int, int]


def test_set_field_round_trips_through_the_cli() -> None:
    class Cfg(BaseModel):
        tags: Annotated[set[str], Field(default={"a"}, kw_only=True)]

    app, seen = _make_app(Cfg)
    assert runner.invoke(app, ["--tags", "x", "--tags", "y"]).exit_code == 0
    assert seen[0].tags == {"x", "y"}


def test_variadic_tuple_field_round_trips_through_the_cli() -> None:
    class Cfg(BaseModel):
        parts: Annotated[tuple[str, ...], Field(default=("a",), kw_only=True)]

    app, seen = _make_app(Cfg)
    assert runner.invoke(app, ["--parts", "x", "--parts", "y"]).exit_code == 0
    assert seen[0].parts == ("x", "y")
