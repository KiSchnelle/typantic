"""Tests for the pydantic_to_typer decorator."""

import inspect
import re
from collections.abc import Callable
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Literal, get_args

import pytest
import typer
from pydantic import AfterValidator, BaseModel, Field, SecretStr
from typer.testing import CliRunner

from typantic import add_command, pydantic_to_typer

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
