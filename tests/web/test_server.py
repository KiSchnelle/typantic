import uvicorn

from typantic.web import server
from typantic.web.launcher import Launcher
from typantic.web.store import JobStore


def test_find_free_port():
    port = server.find_free_port("127.0.0.1")
    assert 1 <= port <= 65535


def test_resolve_token():
    assert server.resolve_token("x", disable=True) is None
    assert server.resolve_token("x", disable=False) == "x"
    generated = server.resolve_token(None, disable=False)
    assert generated is not None
    assert len(generated) > 10


def test_dashboard_url():
    assert server.dashboard_url("h", 80, "tok") == "http://h:80/?token=tok"
    assert server.dashboard_url("h", 80, None) == "http://h:80/"


def test_serve_runs_uvicorn(tmp_path, monkeypatch):
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    launcher = Launcher(JobStore(tmp_path / "jobs"))
    server.serve(launcher, host="127.0.0.1", port=9000, token=None, title="X")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9000
    assert captured["app"] is not None
