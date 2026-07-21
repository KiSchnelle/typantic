import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, Field

from typantic.web import launcher as launcher_mod
from typantic.web.backends.base import Launched, PollResult
from typantic.web.launcher import (
    JobNotTerminalError,
    Launcher,
    UnknownBackendError,
    UnknownCommandError,
    UnknownProjectError,
)
from typantic.web.models import (
    BackendMeta,
    CommandMeta,
    JobRecord,
    JobStatus,
    LaunchRequest,
)
from typantic.web.store import JobStore

META = CommandMeta(app="app", command="run", argv=("run",), title="Run")


class FakeBackend:
    def __init__(self):
        self.launched = []
        self.cancelled = []
        self.poll_count = 0
        self.next_status = JobStatus.RUNNING
        self.poll_result = PollResult(status=JobStatus.RUNNING)

    def launch(self, argv, *, job_dir, log_path, backend_options):
        self.launched.append((argv, backend_options))
        log_path.write_text("hello\n")
        return Launched(pid=4321, status=self.next_status)

    def poll(self, record):
        self.poll_count += 1
        return self.poll_result

    def cancel(self, record):
        self.cancelled.append(record.id)

    def preview(self, argv, *, job_dir, log_path, backend_options):
        return "PREVIEW " + " ".join(argv)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "jobs")
    backend = FakeBackend()
    launcher = Launcher(store, backends={"local": backend})
    return launcher, backend, store


def _request(**over):
    base = {"command_key": "app/run", "backend": "local"}
    return LaunchRequest(**{**base, **over})


# --- discovery / lookup ---


def test_commands(wired):
    launcher, _, _ = wired
    assert [m.key for m in launcher.commands] == ["app/run"]


def test_unknown_command(wired):
    launcher, _, _ = wired
    with pytest.raises(UnknownCommandError):
        launcher.command("missing/cmd")


def test_unknown_backend(wired):
    launcher, _, _ = wired
    with pytest.raises(UnknownBackendError):
        launcher.launch(_request(backend="ghost"))


def test_schema_for(wired):
    launcher, _, _ = wired
    launcher.schema_cache._cache[META.key] = {"title": "Run"}
    assert launcher.schema_for("app/run") == {"title": "Run"}


# --- preview ---


def test_preview(wired):
    launcher, _, _ = wired
    preview = launcher.preview(_request(values={"x": 1, "empty": []}))
    assert json.loads(preview.config) == {"x": 1}  # empty list dropped
    assert preview.argv[0] == "app"
    assert "--config" in preview.argv
    assert preview.script.startswith("PREVIEW")


# --- launch ---


def test_launch_persists_record_and_files(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request(name="my job", values={"x": 1, "empty": []}))
    assert record.backend == "local"
    assert record.pid == 4321
    assert record.status is JobStatus.RUNNING
    assert record.name == "my job"
    # files written
    assert json.loads(store.config_path(record.id).read_text()) == {"x": 1}
    assert store.request_path(record.id).exists()
    # persisted and reloadable
    assert store.load(record.id) == record
    assert backend.launched[0][0][0] == "app"


def test_launch_into_project(wired):
    launcher, _, store = wired
    project = store.create_project("P")
    record = launcher.launch(_request(project_id=project.id))
    assert record.project_id == project.id
    history = store.grouped_history()
    assert history.projects[0].jobs[0].id == record.id


# --- refresh / status ---


def test_refresh_updates_status(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    refreshed = launcher.refresh(record)
    assert refreshed.status is JobStatus.DONE
    assert refreshed.finished_at is not None
    assert refreshed.exit_code == 0


def test_refresh_external_cancel_stamps_the_finish_time(wired):
    # A job cancelled outside the dashboard (scancel, kill) reaches CANCELLED
    # through refresh, and is just as finished as one that failed -- it must not
    # be left showing no finish time at all.
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.CANCELLED)
    refreshed = launcher.refresh(record)
    assert refreshed.status is JobStatus.CANCELLED
    assert refreshed.finished_at is not None


def test_refresh_no_change_returns_same(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.RUNNING)
    assert launcher.refresh(record) is record


def test_refresh_terminal_is_noop(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    done = launcher.refresh(record)
    backend.poll_count = 0
    assert launcher.refresh(done) is done
    assert backend.poll_count == 0


def test_refresh_unknown_backend_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "jobs")
    launcher = Launcher(store, backends={"local": FakeBackend()})
    record = launcher.launch(_request())
    # simulate the backend being uninstalled
    launcher._backends.clear()
    assert launcher.refresh(record) is record


def test_poll_cache_reuses_within_ttl(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    launcher.refresh(record)
    launcher.refresh(record)
    assert backend.poll_count == 1  # second refresh hit the TTL cache


def _bare_record(job_id):
    return JobRecord(
        id=job_id,
        command_key="app/run",
        app="app",
        command="run",
        title="Run",
        backend="local",
        job_dir="/tmp",
        config_path="/tmp/c.json",
        log_path="/tmp/j.log",
        created_at=datetime.now(UTC),
    )


def test_poll_cache_is_bounded(wired, monkeypatch):
    launcher, _, _ = wired
    monkeypatch.setattr(launcher_mod, "_POLL_CACHE_MAX", 3)
    for i in range(10):
        launcher._poll(_bare_record(f"job-{i}"))
    assert len(launcher._poll_cache) == 3
    # LRU: the three most-recently polled ids survive, the earlier ones evicted.
    assert set(launcher._poll_cache) == {"job-7", "job-8", "job-9"}


def test_get_refreshes_from_the_backend(wired):
    launcher, backend, _ = wired
    r1 = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    assert launcher.get(r1.id).status is JobStatus.DONE
    assert launcher.get("missing") is None


# --- cancel ---


def test_cancel(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    cancelled = launcher.cancel(record.id)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert record.id in backend.cancelled


def test_cancel_terminal_is_noop(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    done = launcher.get(record.id)
    assert launcher.cancel(done.id).status is JobStatus.DONE


def test_cancel_missing(wired):
    launcher, _, _ = wired
    assert launcher.cancel("missing") is None


def test_cancel_unknown_backend_still_marks_cancelled(wired):
    launcher, _, _ = wired
    record = launcher.launch(_request())
    launcher._backends.clear()
    assert launcher.cancel(record.id).status is JobStatus.CANCELLED


# --- delete ---


def test_delete_terminal(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    launcher.get(record.id)  # make terminal
    assert launcher.delete(record.id) is True
    assert store.load(record.id) is None


def test_delete_active_cancels_first(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    assert launcher.delete(record.id) is True
    assert record.id in backend.cancelled


def test_delete_active_unknown_backend(wired):
    launcher, _, _ = wired
    record = launcher.launch(_request())
    launcher._backends.clear()
    assert launcher.delete(record.id) is True


def test_delete_missing(wired):
    launcher, _, _ = wired
    assert launcher.delete("missing") is False


def test_query(wired):
    launcher, _, _ = wired
    a = launcher.launch(_request(name="alpha"))
    launcher.launch(_request(name="beta"))
    jobs, total = launcher.query()
    assert total == 2
    jobs, _ = launcher.query(search="alph")
    assert [j.id for j in jobs] == [a.id]
    jobs, total = launcher.query(limit=1)
    assert len(jobs) == 1
    assert total == 2


def test_delete_project_cancels_active_and_deletes_jobs(wired):
    launcher, backend, store = wired
    project = store.create_project("P")
    running = launcher.launch(_request(project_id=project.id))
    assert launcher.delete_project(project.id) is True
    assert running.id in backend.cancelled
    assert store.load(running.id) is None
    assert store.get_project(project.id) is None
    assert launcher.delete_project(project.id) is False


def test_delete_project_skips_cancel_for_terminal_job(wired):
    launcher, backend, store = wired
    project = store.create_project("P")
    rec = launcher.launch(_request(project_id=project.id))
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    launcher.get(rec.id)
    backend.cancelled.clear()
    launcher.delete_project(project.id)
    assert backend.cancelled == []
    assert store.load(rec.id) is None


def test_delete_project_unknown_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "jobs")
    launcher = Launcher(store, backends={"local": FakeBackend()})
    project = store.create_project("P")
    rec = launcher.launch(_request(project_id=project.id))
    launcher._backends.clear()
    assert launcher.delete_project(project.id) is True
    assert store.load(rec.id) is None


# --- request_for / restart ---


def test_request_for_reads_stored_request(wired):
    launcher, _, _ = wired
    record = launcher.launch(_request(name="orig", values={"x": 1}))
    reloaded = launcher.request_for(record.id)
    assert reloaded.name == "orig"
    assert reloaded.values == {"x": 1}


def test_request_for_missing(wired):
    launcher, _, _ = wired
    assert launcher.request_for("missing") is None


def test_request_for_reconstructs_when_file_gone(wired):
    launcher, _, store = wired
    record = launcher.launch(_request(values={"x": 2}))
    store.request_path(record.id).unlink()  # lose the stored request
    reloaded = launcher.request_for(record.id)
    assert reloaded.command_key == "app/run"
    assert reloaded.values == {"x": 2}


def test_restart_reuses_settings(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    launcher.get(record.id)
    restarted = launcher.restart(record.id)
    assert restarted.status is JobStatus.RUNNING
    assert restarted.finished_at is None
    assert restarted.exit_code is None
    assert len(backend.launched) == 2


def test_restart_with_new_request(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request(values={"x": 1}))
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    launcher.get(record.id)
    launcher.restart(record.id, _request(command_key="ignored/x", values={"x": 9}))
    assert json.loads(store.config_path(record.id).read_text()) == {"x": 9}
    # command is fixed by the job, not the new request
    assert store.load(record.id).command_key == "app/run"


def test_restart_active_raises(wired):
    launcher, _, _ = wired
    record = launcher.launch(_request())
    with pytest.raises(JobNotTerminalError):
        launcher.restart(record.id)


def test_restart_missing(wired):
    launcher, _, _ = wired
    assert launcher.restart("missing") is None


def test_refresh_queued_to_running(wired):
    launcher, backend, _ = wired
    backend.next_status = JobStatus.QUEUED
    record = launcher.launch(_request())
    assert record.status is JobStatus.QUEUED
    backend.poll_result = PollResult(status=JobStatus.RUNNING)
    refreshed = launcher.refresh(record)
    assert refreshed.status is JobStatus.RUNNING
    assert refreshed.finished_at is None


def test_backends_meta_no_options(wired):
    launcher, _, _ = wired
    assert launcher.backends_meta() == [BackendMeta(key="local", options_schema=None)]


def test_backends_meta_with_options(tmp_path, monkeypatch):
    class Opts(BaseModel):
        image: str = Field(description="Image.")

    class BoxBackend(FakeBackend):
        options_model = Opts

    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    launcher = Launcher(JobStore(tmp_path / "jobs"), backends={"box": BoxBackend()})
    meta = launcher.backends_meta()
    assert meta[0].key == "box"
    schema = meta[0].options_schema
    assert schema is not None
    assert schema["properties"]["image"]["type"] == "string"


def test_read_values_helper(tmp_path):
    assert launcher_mod._read_values(str(tmp_path / "nope.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert launcher_mod._read_values(str(bad)) == {}
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2]")
    assert launcher_mod._read_values(str(arr)) == {}
    ok = tmp_path / "ok.json"
    ok.write_text('{"a": 1}')
    assert launcher_mod._read_values(str(ok)) == {"a": 1}


def test_launch_with_an_unknown_project_starts_nothing(wired):
    # project_id is a foreign key, so an unknown one used to fail at save() --
    # after the process was already spawned, leaving it running and untracked.
    launcher, backend, store = wired
    with pytest.raises(UnknownProjectError):
        launcher.launch(_request(project_id="does-not-exist"))
    assert backend.launched == []
    assert store.query_jobs()[1] == 0
    assert [p for p in store.root.iterdir() if p.is_dir()] == []


def test_launch_into_a_real_project_is_filed_under_it(wired):
    launcher, _, store = wired
    project = store.create_project("screen A")
    record = launcher.launch(_request(project_id=project.id))
    assert record.project_id == project.id


def test_launch_cleans_up_the_job_dir_when_the_backend_raises(wired):
    launcher, backend, store = wired

    def boom(*args, **kwargs):
        msg = "no scheduler here"
        raise RuntimeError(msg)

    backend.launch = boom
    with pytest.raises(RuntimeError):
        launcher.launch(_request())
    # No row and no orphaned folder left behind.
    assert store.query_jobs()[1] == 0
    assert [p for p in store.root.iterdir() if p.is_dir()] == []


def test_cancel_keeps_the_real_outcome_of_a_just_finished_job(wired):
    # cancel() read the stale stored row, so a job that had already finished was
    # recorded CANCELLED forever.
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    cancelled = launcher.cancel(record.id)
    assert cancelled is not None
    assert cancelled.status is JobStatus.DONE
    assert cancelled.exit_code == 0
