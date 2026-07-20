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


def test_local_server_name_prefers_fqdn(monkeypatch):
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "node01.cluster.example")
    assert server.local_server_name() == "node01.cluster.example"


def test_local_server_name_falls_back_when_localhost(monkeypatch):
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "localhost.localdomain")
    monkeypatch.setattr(server.socket, "gethostname", lambda: "node01")
    assert server.local_server_name() == "node01"


def test_local_server_name_falls_back_when_empty(monkeypatch):
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "")
    monkeypatch.setattr(server.socket, "gethostname", lambda: "node01")
    assert server.local_server_name() == "node01"


def test_ssh_forward_command():
    cmd = server.ssh_forward_command("127.0.0.1", 8123, user="alice", server="node01")
    assert cmd == "ssh -N -L 8123:127.0.0.1:8123 alice@node01"


def test_startup_banner_includes_url_and_tunnel():
    banner = "\n".join(
        server.startup_banner(
            title="catchEM",
            host="127.0.0.1",
            port=8123,
            token="tok",
            user="alice",
            server="node01",
        ),
    )
    assert "catchEM is running." in banner
    assert "http://127.0.0.1:8123/?token=tok" in banner
    assert "ssh -N -L 8123:127.0.0.1:8123 alice@node01" in banner
    assert "keep it private" in banner


def test_startup_banner_omits_token_note_without_token():
    banner = "\n".join(
        server.startup_banner(
            title="X",
            host="127.0.0.1",
            port=8123,
            token=None,
            user="alice",
            server="node01",
        ),
    )
    assert "credential" not in banner
    assert "ssh -N -L" in banner


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
