"""Tests for the pydantic_to_typer decorator."""

import inspect
import re
from collections.abc import Callable
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, get_args

import typer
from pydantic import AfterValidator, BaseModel, Field
from typer.testing import CliRunner

from typantic import pydantic_to_typer

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

    def test_default_factory_called(self) -> None:
        app, _ = _make_app(FullConfig)
        sig = inspect.signature(self._callback(app))
        assert sig.parameters["threshold"].default == 0.5

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
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert "0.5" in _plain(result.output)

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


def _param_meta(app: typer.Typer, name: str) -> object:
    cb = app.registered_commands[0].callback
    assert cb is not None
    return get_args(cb.__annotations__[name])[1]


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
        app, _ = _make_app(FullConfig)
        result = runner.invoke(app, ["--help"])
        assert "0.5" in _plain(result.output)

    def test_none_default_still_passes_none(self) -> None:
        app, results = _make_app(SimpleModel)
        result = runner.invoke(app, ["Alice"])
        assert result.exit_code == 0
        assert results[0].count == 1
