import pytest
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


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "example.com"])  # noqa: S104
def test_serve_refuses_no_token_on_a_routable_host(monkeypatch, tmp_path, host):
    # --no-token is documented "localhost dev only"; on a reachable bind it would
    # publish an unauthenticated job launcher, so serve refuses rather than warns.
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    started = []
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: started.append(k))
    result = runner.invoke(
        app,
        ["serve", "--no-token", "--host", host, "--jobs-dir", str(tmp_path)],
    )
    assert result.exit_code == 2  # a bad flag combination, not a crash
    assert repr(host) in result.output
    assert not started  # never reached the server


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_serve_allows_no_token_on_loopback(monkeypatch, tmp_path, host):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    captured = {}
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: captured.update(k))
    result = runner.invoke(
        app,
        [
            "serve",
            "--no-token",
            "--host",
            host,
            "--port",
            "8123",
            "--jobs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert captured["token"] is None
    assert "no authentication" in result.output  # the banner says so out loud


def test_serve_allows_a_routable_host_when_a_token_is_set(monkeypatch, tmp_path):
    # The guard is about missing auth, not about binding: with a token, a
    # reachable host is a legitimate deployment.
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    captured = {}
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: captured.update(k))
    result = runner.invoke(
        app,
        [
            "serve",
            "--token",
            "shh",
            "--host",
            "0.0.0.0",  # noqa: S104 - the point of the test
            "--port",
            "8123",
            "--jobs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert captured["token"] == "shh"


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


def test_an_unknown_log_level_is_a_usage_error(monkeypatch, tmp_path):
    # It was accepted, the banner printed, and uvicorn then failed with a
    # KeyError traceback.
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    started = []
    monkeypatch.setattr(cli_mod, "serve", lambda launcher, **k: started.append(k))
    result = runner.invoke(
        app,
        ["serve", "--no-token", "--jobs-dir", str(tmp_path), "--log-level", "loud"],
    )
    assert result.exit_code == 2
    assert "loud" in result.output
    assert started == []
