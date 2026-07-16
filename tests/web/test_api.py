import asyncio
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from typantic.web import api as api_mod
from typantic.web import gallery
from typantic.web import launcher as launcher_mod
from typantic.web.api import _tail_log, make_api
from typantic.web.backends.base import Launched, PollResult
from typantic.web.backends.slurm import SlurmBackend
from typantic.web.models import CommandMeta, JobRecord, JobStatus
from typantic.web.store import JobStore

META = CommandMeta(app="app", command="run", argv=("run",), title="Run")
AUTH = {"Authorization": "Bearer secret"}
BAD_AUTH = {"Authorization": "Bearer wrong"}


class FakeBackend:
    def __init__(self):
        self.poll_result = PollResult(status=JobStatus.RUNNING)

    def launch(self, argv, *, job_dir, log_path, backend_options):
        log_path.write_text("hello\n")
        return Launched(pid=4321, status=JobStatus.RUNNING)

    def poll(self, record):
        return self.poll_result

    def cancel(self, record):
        pass

    def preview(self, argv, *, job_dir, log_path, backend_options):
        return "PREVIEW"


def _slurm():
    def runner(argv):
        if argv[0] == "sbatch":
            return subprocess.CompletedProcess(argv, 0, "999\n", "")
        return subprocess.CompletedProcess(argv, 0, "COMPLETED|0:0", "")

    return SlurmBackend(runner)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "jobs")
    backend = FakeBackend()
    launcher = launcher_mod.Launcher(
        store,
        backends={"local": backend, "slurm": _slurm()},
    )
    client = TestClient(make_api(launcher, token="secret", title="Test UI"))
    return SimpleNamespace(
        client=client,
        launcher=launcher,
        store=store,
        backend=backend,
    )


def _launch(env, **over):
    body = {"command_key": "app/run", "backend": "local", **over}
    resp = env.client.post("/api/launch", json=body, headers=AUTH)
    assert resp.status_code == 200
    return resp.json()


# --- auth ---


def test_requires_token(env):
    assert env.client.get("/api/commands").status_code == 401
    assert env.client.get("/api/commands", headers=BAD_AUTH).status_code == 401
    assert env.client.get("/api/commands", headers=AUTH).status_code == 200
    assert env.client.get("/api/commands?token=secret").status_code == 200


def test_no_token_configured_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    launcher = launcher_mod.Launcher(
        JobStore(tmp_path / "j"),
        backends={"local": FakeBackend()},
    )
    client = TestClient(make_api(launcher, token=None))
    assert client.get("/api/commands").status_code == 200


# --- meta / commands / schema ---


def test_meta(env):
    data = env.client.get("/api/meta", headers=AUTH).json()
    assert data["title"] == "Test UI"
    assert data["version"]
    keys = {b["key"] for b in data["backends"]}
    assert keys == {"local", "slurm"}
    slurm_meta = next(b for b in data["backends"] if b["key"] == "slurm")
    assert slurm_meta["options_schema"]["properties"]["partition"]


def test_list_commands(env):
    assert env.client.get("/api/commands", headers=AUTH).json()[0]["key"] == "app/run"


def test_command_schema_cached(env):
    env.launcher.schema_cache._cache["app/run"] = {"title": "Run"}
    resp = env.client.get("/api/commands/app/run/schema", headers=AUTH)
    assert resp.json() == {"title": "Run"}


def test_command_schema_unknown(env):
    resp = env.client.get("/api/commands/nope/x/schema", headers=AUTH)
    assert resp.status_code == 404


def test_command_schema_fetch_error_is_502(env):
    # "app" is not a real executable on PATH -> SchemaError -> 502.
    resp = env.client.get("/api/commands/app/run/schema", headers=AUTH)
    assert resp.status_code == 502


# --- launch / preview ---


def test_launch(env):
    record = _launch(env, name="job1")
    assert record["backend"] == "local"
    assert record["status"] == "running"


def test_launch_unknown_command(env):
    body = {"command_key": "no/cmd", "backend": "local"}
    assert env.client.post("/api/launch", json=body, headers=AUTH).status_code == 404


def test_launch_unknown_backend(env):
    body = {"command_key": "app/run", "backend": "ghost"}
    assert env.client.post("/api/launch", json=body, headers=AUTH).status_code == 400


def test_launch_invalid_backend_options_is_422(env):
    body = {
        "command_key": "app/run",
        "backend": "slurm",
        "backend_options": {"gpus": -1},
    }
    assert env.client.post("/api/launch", json=body, headers=AUTH).status_code == 422


def test_preview(env):
    body = {"command_key": "app/run", "backend": "local", "values": {"x": 1}}
    resp = env.client.post("/api/preview", json=body, headers=AUTH)
    assert resp.json()["script"] == "PREVIEW"


def test_preview_unknown_command(env):
    body = {"command_key": "no/cmd", "backend": "local"}
    assert env.client.post("/api/preview", json=body, headers=AUTH).status_code == 404


def test_preview_unknown_backend(env):
    body = {"command_key": "app/run", "backend": "ghost"}
    assert env.client.post("/api/preview", json=body, headers=AUTH).status_code == 400


def test_preview_invalid_backend_options_is_422(env):
    body = {
        "command_key": "app/run",
        "backend": "slurm",
        "backend_options": {"gpus": -1},
    }
    assert env.client.post("/api/preview", json=body, headers=AUTH).status_code == 422


# --- jobs ---


def test_list_and_get_job(env):
    record = _launch(env)
    page = env.client.get("/api/jobs", headers=AUTH).json()
    assert page["total"] == 1
    assert page["jobs"][0]["id"] == record["id"]
    assert env.client.get(f"/api/jobs/{record['id']}", headers=AUTH).status_code == 200
    assert env.client.get("/api/jobs/missing", headers=AUTH).status_code == 404


def test_jobs_query(env):
    a = _launch(env, name="alpha")
    _launch(env, name="beta")
    hits = env.client.get("/api/jobs?q=alph", headers=AUTH).json()
    assert [j["id"] for j in hits["jobs"]] == [a["id"]]
    assert env.client.get("/api/jobs?status=running", headers=AUTH).json()["total"] == 2
    paged = env.client.get("/api/jobs?limit=1", headers=AUTH).json()
    assert len(paged["jobs"]) == 1
    assert paged["total"] == 2
    asc = env.client.get("/api/jobs?sort=name&order=asc", headers=AUTH).json()
    assert [j["name"] for j in asc["jobs"]] == ["alpha", "beta"]
    assert env.client.get("/api/jobs?backend=local", headers=AUTH).json()["total"] == 2
    assert env.client.get("/api/jobs?status=bogus", headers=AUTH).status_code == 422


def test_jobs_query_by_project_and_ungrouped(env):
    project = env.client.post("/api/projects", json={"name": "P"}, headers=AUTH).json()
    filed = _launch(env, project_id=project["id"])
    _launch(env)  # ungrouped
    in_proj = env.client.get(f"/api/jobs?project={project['id']}", headers=AUTH).json()
    assert [j["id"] for j in in_proj["jobs"]] == [filed["id"]]
    ungrouped = env.client.get("/api/jobs?ungrouped=true", headers=AUTH).json()
    assert filed["id"] not in {j["id"] for j in ungrouped["jobs"]}


def test_delete_project_deletes_its_jobs(env):
    project = env.client.post("/api/projects", json={"name": "P"}, headers=AUTH).json()
    job = _launch(env, project_id=project["id"])
    pid = project["id"]
    assert env.client.delete(f"/api/projects/{pid}", headers=AUTH).status_code == 200
    assert env.client.get(f"/api/jobs/{job['id']}", headers=AUTH).status_code == 404
    assert env.client.delete(f"/api/projects/{pid}", headers=AUTH).status_code == 404


def test_cancel_job(env):
    record = _launch(env)
    resp = env.client.post(f"/api/jobs/{record['id']}/cancel", headers=AUTH)
    assert resp.json()["status"] == "cancelled"
    assert env.client.post("/api/jobs/missing/cancel", headers=AUTH).status_code == 404


def test_delete_job(env):
    record = _launch(env)
    resp = env.client.delete(f"/api/jobs/{record['id']}", headers=AUTH)
    assert resp.json() == {"deleted": record["id"]}
    assert env.client.delete("/api/jobs/missing", headers=AUTH).status_code == 404


def test_job_request(env):
    record = _launch(env, name="orig")
    resp = env.client.get(f"/api/jobs/{record['id']}/request", headers=AUTH)
    assert resp.json()["name"] == "orig"
    assert env.client.get("/api/jobs/missing/request", headers=AUTH).status_code == 404


def _make_terminal(env, record):
    env.backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    env.client.get(f"/api/jobs/{record['id']}", headers=AUTH)


def test_restart_job(env):
    record = _launch(env)
    _make_terminal(env, record)
    resp = env.client.post(f"/api/jobs/{record['id']}/restart", headers=AUTH)
    assert resp.json()["status"] == "running"


def test_restart_active_is_409(env):
    record = _launch(env)
    resp = env.client.post(f"/api/jobs/{record['id']}/restart", headers=AUTH)
    assert resp.status_code == 409


def test_restart_unknown_backend_is_400(env):
    record = _launch(env)
    _make_terminal(env, record)
    body = {"command_key": "app/run", "backend": "ghost"}
    resp = env.client.post(f"/api/jobs/{record['id']}/restart", json=body, headers=AUTH)
    assert resp.status_code == 400


def test_restart_missing(env):
    assert env.client.post("/api/jobs/missing/restart", headers=AUTH).status_code == 404


# --- images ---


def test_job_images_and_image(env, monkeypatch):
    monkeypatch.setattr(gallery, "_THUMB_CACHE", env.store.root / "cache")
    record = _launch(env)
    Image.new("RGB", (8, 8)).save(env.store.job_dir(record["id"]) / "out.png")

    images = env.client.get(f"/api/jobs/{record['id']}/images", headers=AUTH).json()
    assert images["images"][0]["name"] == "out.png"

    url = f"/api/jobs/{record['id']}/image?root=0&path=out.png"
    assert env.client.get(url, headers=AUTH).status_code == 200
    thumb = env.client.get(url + "&w=32", headers=AUTH)
    assert thumb.headers["content-type"] == "image/webp"


def test_job_image_thumbnail_falls_back_to_full(env, monkeypatch):
    monkeypatch.setattr(gallery, "_THUMB_CACHE", env.store.root / "cache")
    record = _launch(env)
    # a .png that Pillow can't decode -> thumbnail None -> serve the original
    (env.store.job_dir(record["id"]) / "broken.png").write_text("not an image")
    url = f"/api/jobs/{record['id']}/image?root=0&path=broken.png&w=32"
    assert env.client.get(url, headers=AUTH).status_code == 200


def test_job_images_missing_job(env):
    assert env.client.get("/api/jobs/missing/images", headers=AUTH).status_code == 404


def test_job_image_missing_job(env):
    resp = env.client.get("/api/jobs/missing/image?root=0&path=x.png", headers=AUTH)
    assert resp.status_code == 404


def test_job_image_bad_path(env):
    record = _launch(env)
    url = f"/api/jobs/{record['id']}/image?root=0&path=ghost.png"
    assert env.client.get(url, headers=AUTH).status_code == 404


# --- projects / history ---


def test_projects_crud(env):
    created = env.client.post(
        "/api/projects", json={"name": "Screen A"}, headers=AUTH,
    ).json()
    assert created["name"] == "Screen A"
    listing = env.client.get("/api/projects", headers=AUTH).json()
    assert listing[0]["id"] == created["id"]
    pid = created["id"]
    assert env.client.delete(f"/api/projects/{pid}", headers=AUTH).status_code == 200
    assert env.client.delete(f"/api/projects/{pid}", headers=AUTH).status_code == 404


def test_history(env):
    project = env.client.post("/api/projects", json={"name": "P"}, headers=AUTH).json()
    _launch(env, project_id=project["id"])
    _launch(env)
    history = env.client.get("/api/history", headers=AUTH).json()
    assert history["projects"][0]["project"]["id"] == project["id"]
    assert len(history["ungrouped"]) == 1


# --- filesystem picker ---


def test_browse(env, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x")
    data = env.client.get(f"/api/fs?path={tmp_path}", headers=AUTH).json()
    assert {"sub", "file.txt"} <= {e["name"] for e in data["entries"]}
    assert data["path"] == str(tmp_path.resolve())


def test_browse_no_path_uses_home(env):
    data = env.client.get("/api/fs", headers=AUTH).json()
    assert data["path"]


def test_browse_file_opens_parent(env, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    data = env.client.get(f"/api/fs?path={f}", headers=AUTH).json()
    assert data["path"] == str(tmp_path.resolve())


def test_browse_root_has_no_parent(env):
    data = env.client.get("/api/fs?path=/", headers=AUTH).json()
    assert data["parent"] is None


def test_browse_unreadable_dir_reports_error(env, tmp_path, monkeypatch):
    def boom(_path):
        raise OSError

    monkeypatch.setattr(api_mod.os, "scandir", boom)
    data = env.client.get(f"/api/fs?path={tmp_path}", headers=AUTH).json()
    assert data["error"] is not None


def test_browse_entry_error_is_skipped(env, tmp_path, monkeypatch):
    class BadEntry:
        name = "weird"

        def is_dir(self):
            raise OSError

    class Scan:
        def __enter__(self):
            return [BadEntry()]

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(api_mod.os, "scandir", lambda _p: Scan())
    data = env.client.get(f"/api/fs?path={tmp_path}", headers=AUTH).json()
    assert {"name": "weird", "is_dir": False} in data["entries"]


def test_mkdir(env, tmp_path):
    body = {"path": str(tmp_path), "name": "new"}
    resp = env.client.post("/api/fs/mkdir", json=body, headers=AUTH)
    assert resp.status_code == 200
    assert (tmp_path / "new").is_dir()


def test_mkdir_reserved_name(env, tmp_path):
    body = {"path": str(tmp_path), "name": "."}
    assert env.client.post("/api/fs/mkdir", json=body, headers=AUTH).status_code == 400


def test_mkdir_separator_in_name(env, tmp_path):
    body = {"path": str(tmp_path), "name": "a/b"}
    assert env.client.post("/api/fs/mkdir", json=body, headers=AUTH).status_code == 400


def test_mkdir_missing_parent(env, tmp_path):
    body = {"path": str(tmp_path / "ghost"), "name": "x"}
    assert env.client.post("/api/fs/mkdir", json=body, headers=AUTH).status_code == 400


def test_mkdir_collides_with_file(env, tmp_path):
    (tmp_path / "foo").write_text("x")
    body = {"path": str(tmp_path), "name": "foo"}
    assert env.client.post("/api/fs/mkdir", json=body, headers=AUTH).status_code == 400


# --- log tail WebSocket (route wrapper) ---


def test_log_tail(env):
    record = _launch(env)
    env.backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    frames = []
    with env.client.websocket_connect(
        f"/ws/jobs/{record['id']}/log?token=secret",
    ) as ws:
        while True:
            msg = ws.receive_json()
            frames.append(msg)
            if "end" in msg:
                break
    assert any("hello" in f.get("log", "") for f in frames)
    assert frames[-1]["end"]["status"] == "done"


def test_log_tail_bad_token(env):
    record = _launch(env)
    url = f"/ws/jobs/{record['id']}/log?token=wrong"
    with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(url) as ws:
        ws.receive_json()


def test_log_tail_missing_job(env):
    url = "/ws/jobs/missing/log?token=secret"
    with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(url) as ws:
        ws.receive_json()


# --- _tail_log directly (all branches, no network timing) ---


class FakeWS:
    def __init__(self, fail_after=None):
        self.sent = []
        self.closed = False
        self.fail_after = fail_after

    async def send_json(self, data):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise WebSocketDisconnect(1000)
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _rec(status):
    return JobRecord(
        id="j",
        command_key="a/b",
        app="a",
        command="b",
        title="T",
        backend="local",
        job_dir="/x",
        config_path="/x/c",
        log_path="/x/job.log",
        status=status,
        created_at=datetime.now(UTC),
    )


def test_tail_streams_then_ends(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("hello\n")
    launcher = SimpleNamespace(get=lambda _id: _rec(JobStatus.DONE))
    ws = FakeWS()
    asyncio.run(_tail_log(ws, launcher, "j", log, interval=0))
    assert {"log": "hello\n"} in ws.sent
    assert ws.sent[-1] == {"end": {"status": JobStatus.DONE}}
    assert ws.closed


def test_tail_missing_record_and_log(tmp_path):
    launcher = SimpleNamespace(get=lambda _id: None)
    ws = FakeWS()
    asyncio.run(_tail_log(ws, launcher, "j", tmp_path / "gone.log", interval=0))
    assert ws.sent == [{"end": {"status": "unknown"}}]
    assert ws.closed


def test_tail_flushes_final_chunk(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("")
    calls = {"n": 0}

    def get(_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return _rec(JobStatus.RUNNING)
        log.write_text("finalchunk\n")  # appears just before the terminal tail read
        return _rec(JobStatus.DONE)

    ws = FakeWS()
    asyncio.run(_tail_log(ws, SimpleNamespace(get=get), "j", log, interval=0))
    assert {"log": "finalchunk\n"} in ws.sent


def test_tail_client_disconnect(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("hi\n")
    launcher = SimpleNamespace(get=lambda _id: _rec(JobStatus.RUNNING))
    ws = FakeWS(fail_after=0)  # first send raises WebSocketDisconnect
    asyncio.run(_tail_log(ws, launcher, "j", log, interval=0))
    assert not ws.closed


# --- app wiring: extra routers, SPA, helpers ---


def _bare_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    return launcher_mod.Launcher(
        JobStore(tmp_path / "j"),
        backends={"local": FakeBackend()},
    )


def test_extra_routers_are_mounted(tmp_path, monkeypatch):
    router = APIRouter()

    @router.get("/api/extra")
    def extra() -> dict[str, bool]:
        return {"ok": True}

    launcher = _bare_launcher(tmp_path, monkeypatch)
    client = TestClient(make_api(launcher, token=None, extra_routers=[router]))
    assert client.get("/api/extra").json() == {"ok": True}


def test_serves_spa_when_present(tmp_path, monkeypatch):
    spa = tmp_path / "web_dist"
    spa.mkdir()
    (spa / "index.html").write_text("<html>hi</html>")
    monkeypatch.setattr(api_mod, "_SPA_DIR", spa)
    launcher = _bare_launcher(tmp_path, monkeypatch)
    client = TestClient(make_api(launcher, token=None))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "hi" in resp.text


def test_no_spa_when_dashboard_disabled(tmp_path, monkeypatch):
    # dashboard=False never mounts the SPA, even if web_dist exists on disk.
    launcher = _bare_launcher(tmp_path, monkeypatch)
    client = TestClient(make_api(launcher, token=None, dashboard=False))
    assert client.get("/").status_code == 404


def test_is_dir_handles_oserror():
    class BadPath:
        def is_dir(self):
            raise OSError

    assert api_mod._is_dir(BadPath()) is False


def test_browse_nonexistent_path_uses_home(env):
    data = env.client.get("/api/fs?path=/no/such/dir/xyz", headers=AUTH).json()
    assert data["path"] != "/no/such/dir/xyz"
