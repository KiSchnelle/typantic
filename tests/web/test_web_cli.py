from typer.testing import CliRunner

from typantic.web import cli as cli_mod
from typantic.web import launcher as launcher_mod
from typantic.web.cli import app
from typantic.web.models import CommandMeta

runner = CliRunner()
META = CommandMeta(app="app", command="run", argv=("run",), title="Run")


def test_serve_no_token(monkeypatch, tmp_path):
    # a discovered command -> the "no commands" warning branch is skipped
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    captured = {}
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: captured.update(k))
    result = runner.invoke(
        app,
        [
            "serve",
            "--no-token",
            "--jobs-dir",
            str(tmp_path),
            "--port",
            "8123",
            "--title",
            "My UI",
        ],
    )
    assert result.exit_code == 0
    assert captured["token"] is None
    assert captured["port"] == 8123
    assert captured["title"] == "My UI"
    assert captured["log_level"] == "info"  # the default, threaded through
    assert "My UI is running" in result.output


def test_serve_log_level_is_threaded_through(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    captured = {}
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: captured.update(k))
    result = runner.invoke(
        app,
        ["serve", "--no-token", "--jobs-dir", str(tmp_path), "--log-level", "debug"],
    )
    assert result.exit_code == 0
    assert captured["log_level"] == "debug"


def test_serve_default_token_and_port(monkeypatch, tmp_path):
    # no discovered commands -> exercises the "no commands" warning branch,
    # without depending on whatever entry points the test env happens to expose
    monkeypatch.setattr(launcher_mod, "discover_commands", list)
    monkeypatch.setattr(cli_mod, "serve", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "find_free_port", lambda host: 55555)
    result = runner.invoke(app, ["serve", "--jobs-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "token=" in result.output
    assert "55555" in result.output
