import contextlib
import logging
import os
import sys

import pytest
import typer
from pydantic import BaseModel

from typantic import add_command, make_main
from typantic._main import _is_autocompleting, _wants_version

_PKG = "typantic"  # installed (editable), so version() resolves


def _spy_context(events):
    @contextlib.contextmanager
    def run_context():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    return run_context


# ---------------------------------------------------------------------------
# _wants_version
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("args", [["--version"], ["-V"], ["version"]])
def test_wants_version_true(args):
    assert _wants_version(args)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["run"],
        ["run", "--version"],
        ["run", "-V"],
        # A single-command app's own `version` field: `app --version 2.1` is a
        # run, not a version request.
        ["--version", "2.1"],
        ["--foo", "--version"],
    ],
)
def test_wants_version_false(args):
    # Only a lone version token asks for the package version; anything else is
    # left for Typer to parse.
    assert not _wants_version(args)


# ---------------------------------------------------------------------------
# _is_autocompleting
# ---------------------------------------------------------------------------
def test_is_autocompleting_detects_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["myprog"])
    monkeypatch.setenv("_MYPROG_COMPLETE", "complete_zsh")
    assert _is_autocompleting()


@pytest.mark.parametrize("prog", ["my-prog", "my.tool", "my-prog.exe", "plain"])
def test_is_autocompleting_matches_typer_variable(monkeypatch, prog):
    # The variable must be the one Typer itself reads, or a completion request
    # falls through to a real run. Typer maps only "-" to "_" (click also maps
    # "."), so "my.tool" is completed through _MY.TOOL_COMPLETE.
    monkeypatch.setattr(sys, "argv", [f"/usr/local/bin/{prog}"])
    monkeypatch.setenv(f"_{prog}_COMPLETE".replace("-", "_").upper(), "source_bash")
    assert _is_autocompleting()


def test_is_not_autocompleting_for_normal_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["myprog", "run"])
    for key in [k for k in os.environ if k.endswith("_COMPLETE")]:
        monkeypatch.delenv(key, raising=False)
    assert not _is_autocompleting()


# ---------------------------------------------------------------------------
# make_main: version short-circuit
# ---------------------------------------------------------------------------
def test_version_short_circuit_skips_load_app(monkeypatch, capsys):
    loaded = False

    def loader():
        nonlocal loaded
        loaded = True
        return typer.Typer()

    monkeypatch.setattr(sys, "argv", ["typantic", "--version"])
    make_main(loader, package_name=_PKG)()
    assert not loaded  # heavy command modules never imported
    assert capsys.readouterr().out.strip()  # a version was printed


def test_version_short_circuit_skips_run_context(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(sys, "argv", ["typantic", "--version"])
    make_main(
        typer.Typer,
        package_name=_PKG,
        run_context=_spy_context(events),
    )()
    capsys.readouterr()
    assert not events  # answered from metadata, nothing set up


# ---------------------------------------------------------------------------
# make_main: run-path exit handling
# ---------------------------------------------------------------------------
def _app_that(exit_code=None, *, crash=False):
    app = typer.Typer()

    @app.command()
    def go():
        if crash:
            msg = "boom"
            raise ValueError(msg)
        if exit_code:
            raise typer.Exit(exit_code)

    return app


def test_clean_exit_is_swallowed(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["typantic"])
    make_main(_app_that, package_name=_PKG)()  # returns, no raise


def test_runs_without_a_run_context(monkeypatch, caplog):
    # run_context is optional; with none the run still happens and is timed.
    monkeypatch.setattr(sys, "argv", ["typantic"])
    with caplog.at_level(logging.INFO, logger=_PKG):
        make_main(_app_that, package_name=_PKG, run_context=None)()
    assert "Execution took" in caplog.text


def test_run_context_wraps_a_clean_run(monkeypatch):
    events = []
    monkeypatch.setattr(sys, "argv", ["typantic"])
    make_main(_app_that, package_name=_PKG, run_context=_spy_context(events))()
    assert events == ["enter", "exit"]


def test_run_context_exits_when_the_command_crashes(monkeypatch):
    # The context must be torn down even when the command raises -- that is the
    # whole reason a caller passes one (flushing a logger, say).
    events = []
    monkeypatch.setattr(sys, "argv", ["typantic"])
    with pytest.raises(SystemExit):
        make_main(
            lambda: _app_that(crash=True),
            package_name=_PKG,
            run_context=_spy_context(events),
        )()
    assert events == ["enter", "exit"]


def test_real_clean_run_logs_timing(monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["typantic"])
    with caplog.at_level(logging.INFO, logger=_PKG):
        make_main(_app_that, package_name=_PKG)()
    assert "Execution took" in caplog.text  # real run: wall-clock logged


def test_meta_flag_exit_skips_timing_log(monkeypatch, caplog):
    # A meta op (--help/--schema/--generate-config) exits 0 without a real run;
    # its timing must NOT be logged so machine-readable stdout stays clean.
    monkeypatch.setattr(sys, "argv", ["typantic", "--help"])
    with caplog.at_level(logging.INFO, logger=_PKG):
        make_main(_app_that, package_name=_PKG)()  # --help exits 0, swallowed
    assert "Execution took" not in caplog.text


def test_nonzero_exit_is_reraised(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["typantic"])
    with pytest.raises(SystemExit) as exc:
        make_main(lambda: _app_that(exit_code=2), package_name=_PKG)()
    assert exc.value.code == 2


def test_handler_exception_becomes_exit_1(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["typantic"])
    with pytest.raises(SystemExit) as exc:
        make_main(lambda: _app_that(crash=True), package_name=_PKG)()
    assert exc.value.code == 1


def test_autocomplete_skips_run_context(monkeypatch):
    # Completion should run the app but NOT enter the run context.
    events = []
    monkeypatch.setattr("typantic._main._is_autocompleting", lambda: True)

    ran = []

    def loader():
        app = typer.Typer()

        @app.command()
        def go():
            ran.append(True)

        return app

    monkeypatch.setattr(sys, "argv", ["typantic"])
    with pytest.raises(SystemExit):
        make_main(loader, package_name=_PKG, run_context=_spy_context(events))()
    assert ran  # the app was run (completion path)
    assert not events  # run context was skipped


def test_autocomplete_returns_without_entering_run_context(monkeypatch):
    # If the completion machinery returns instead of exiting, main() returns via
    # the completion short-circuit without ever entering the run context.
    monkeypatch.setattr("typantic._main._is_autocompleting", lambda: True)
    monkeypatch.setattr(sys, "argv", ["typantic"])
    ran = []

    def fake_app():  # a completion callable that returns rather than exits
        ran.append(True)

    make_main(lambda: fake_app, package_name=_PKG)()  # returns, no raise
    assert ran


def test_autocomplete_import_error_propagates(monkeypatch):
    # The completion path runs load_app() outside the run context, so a loader
    # import error there propagates raw (the shell just gets no completions)
    # rather than being reshaped into exit 1 like a real run's import failure.
    monkeypatch.setattr("typantic._main._is_autocompleting", lambda: True)
    monkeypatch.setattr(sys, "argv", ["typantic"])

    def loader():
        msg = "no torch"
        raise ModuleNotFoundError(msg)

    with pytest.raises(ModuleNotFoundError):
        make_main(loader, package_name=_PKG)()


def test_load_app_import_error_becomes_exit_1(monkeypatch):
    # A failed (heavy) command import is logged and exits 1, not a raw traceback
    # escaping the run context.
    def loader():
        msg = "no torch"
        raise ModuleNotFoundError(msg)

    monkeypatch.setattr(sys, "argv", ["typantic"])
    with pytest.raises(SystemExit) as exc:
        make_main(loader, package_name=_PKG)()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# make_main: meta flags, a version field, Ctrl-C while loading
# ---------------------------------------------------------------------------
class _Settings(BaseModel):
    version: str = "1.0"
    name: str = "x"


def _single_command_app(seen):
    app = typer.Typer()
    add_command(app, _Settings, seen.append, name="go", config_file=True)
    return app


def test_a_version_field_is_not_mistaken_for_a_version_request(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(sys, "argv", ["prog", "--version", "2.1"])
    make_main(lambda: _single_command_app(seen), package_name=_PKG)()
    capsys.readouterr()
    assert seen[0].version == "2.1"


@pytest.mark.parametrize(
    "meta",
    [
        ["--help"],
        ["--schema"],
        ["--generate-config", "{tmp}/t.yaml"],
        ["--generate-config={tmp}/t.yaml"],
    ],
)
def test_meta_flags_skip_the_run_context_and_the_timing(
    monkeypatch,
    caplog,
    capsys,
    tmp_path,
    meta,
):
    # A run context that logs to stdout would otherwise corrupt --schema's JSON,
    # which the web launcher parses.
    events = []
    argv = [arg.format(tmp=tmp_path) for arg in meta]
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    with caplog.at_level(logging.INFO, logger=_PKG):
        make_main(
            lambda: _single_command_app([]),
            package_name=_PKG,
            run_context=_spy_context(events),
        )()
    capsys.readouterr()
    assert not events
    assert "Execution took" not in caplog.text


def test_ctrl_c_while_loading_the_app_exits_130(monkeypatch):
    def loader():
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["prog"])
    with pytest.raises(SystemExit) as exc:
        make_main(loader, package_name=_PKG)()
    assert exc.value.code == 130
