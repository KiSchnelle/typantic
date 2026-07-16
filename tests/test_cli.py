import builtins
import sys

import pytest
from typer.testing import CliRunner

from typantic import __version__
from typantic._cli import _version_callback, app, main

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_version_callback_noop():
    assert _version_callback(value=False) is None


def test_help_shows_usage():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_main_entry_point(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["typantic", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0


def test_web_help_lists_serve():
    result = runner.invoke(app, ["web", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_web_without_extra_prints_hint(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "typantic.web.cli":
            msg = "No module named 'fastapi'"
            raise ModuleNotFoundError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["web", "serve"])
    assert result.exit_code == 1
    assert "typantic[web]" in result.output
