import os
import time

import uvicorn

from typantic.web import gallery, server
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


def test_is_loopback_host():
    # Loopback literals and the localhost alias: safe to serve without auth.
    assert server.is_loopback_host("127.0.0.1")
    assert server.is_loopback_host("127.0.1.5")  # the whole 127/8 block
    assert server.is_loopback_host("::1")
    assert server.is_loopback_host("localhost")
    # Everything else is treated as reachable, including the wildcards and any
    # hostname this process cannot resolve -- guessing wrong the other way would
    # leave an unauthenticated dashboard on the network.
    assert not server.is_loopback_host("0.0.0.0")  # noqa: S104 - asserting it is refused
    assert not server.is_loopback_host("::")
    assert not server.is_loopback_host("example.com")
    assert not server.is_loopback_host("")


def test_startup_banner_warns_when_unauthenticated():
    with_token = "\n".join(
        server.startup_banner(
            title="T", host="127.0.0.1", port=1, token="tok", user="u", server="s"
        ),
    )
    assert "keep it private" in with_token
    assert "no authentication" not in with_token

    without = "\n".join(
        server.startup_banner(
            title="T", host="127.0.0.1", port=1, token=None, user="u", server="s"
        ),
    )
    assert "no authentication" in without
    assert "launch jobs as you" in without


def test_dashboard_url():
    assert server.dashboard_url("h", 80, "tok") == "http://h:80/?token=tok"
    assert server.dashboard_url("h", 80, None) == "http://h:80/"


def test_local_server_name_prefers_fqdn(monkeypatch):
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "node01.cluster.example")
    assert server.local_server_name() == "node01.cluster.example"


def test_local_server_name_uses_hostname_f_when_getfqdn_short(monkeypatch):
    # getfqdn returns only the bare name (the reported `grobi` behaviour) ->
    # `hostname -f` supplies the qualified name
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "grobi")
    monkeypatch.setattr(server, "_fqdn_via_hostname", lambda: "grobi.biologie.uos.de")
    assert server.local_server_name() == "grobi.biologie.uos.de"


def test_local_server_name_falls_back_to_bare_name(monkeypatch):
    # neither source qualified -> bare node name
    monkeypatch.setattr(server.socket, "getfqdn", lambda: "grobi")
    monkeypatch.setattr(server, "_fqdn_via_hostname", lambda: "")
    monkeypatch.setattr(server.socket, "gethostname", lambda: "grobi")
    assert server.local_server_name() == "grobi"


def test_fqdn_via_hostname_reads_command(monkeypatch):
    class _Result:
        stdout = "grobi.biologie.uos.de\n"

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: _Result())
    assert server._fqdn_via_hostname() == "grobi.biologie.uos.de"


def test_fqdn_via_hostname_swallows_failure(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(server.subprocess, "run", _boom)
    assert server._fqdn_via_hostname() == ""


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


def test_serve_prunes_the_thumbnail_cache_first(tmp_path, monkeypatch):
    stale = gallery._cache_dir() / "stale.webp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    old = time.time() - 31 * 86400
    os.utime(stale, (old, old))
    monkeypatch.setattr(uvicorn, "run", lambda _app, **_kwargs: None)
    server.serve(
        Launcher(JobStore(tmp_path / "jobs")), host="127.0.0.1", port=9000, token=None
    )
    assert not stale.exists()
