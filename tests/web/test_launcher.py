import json
import sqlite3
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from typantic.web import launcher as launcher_mod
from typantic.web.backends.base import Launched, LaunchUncertainError, PollResult
from typantic.web.backends.scheduler import SchedulerError
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
        # Runs in place of the poll, to act out what happens while a slow
        # status query (sacct can take seconds) is in flight.
        self.on_poll = None
        # A scheduler job writes its log only once it starts running.
        self.writes_log = True
        self.rejects = None  # an exception preview() and launch() raise
        self.cancel_error = None  # an exception cancel() raises

    def launch(self, argv, *, job_dir, log_path, backend_options):
        if self.rejects is not None:
            raise self.rejects
        pid = 4321 + len(self.launched)  # each run gets its own process
        self.launched.append((argv, backend_options))
        if self.writes_log:
            log_path.write_text("hello\n")
        return Launched(pid=pid, status=self.next_status)

    def poll(self, record):
        self.poll_count += 1
        if self.on_poll is not None:
            return self.on_poll(record)
        return self.poll_result

    def cancel(self, record):
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(record.id)

    def preview(self, argv, *, job_dir, log_path, backend_options):
        if self.rejects is not None:
            raise self.rejects
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


# --- a relative jobs root still launches (a real process, from its own folder) ---


def test_a_job_under_a_relative_jobs_root_finds_its_config(tmp_path, monkeypatch):
    # The job runs with its folder as cwd, so a relative --config path (and the
    # exit marker's) resolved a second time inside it: with `--jobs-dir ./jobs`
    # every job failed, unable to find its own config.
    from typantic.web.backends.local import LocalBackend  # noqa: PLC0415

    probe = CommandMeta(
        app="sh", command="probe", argv=("-c", 'test -f "$2"', "sh"), title="Probe"
    )
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [probe])
    monkeypatch.chdir(tmp_path)
    launcher = Launcher(JobStore(Path("jobs")), backends={"local": LocalBackend()})
    record = launcher.launch(LaunchRequest(command_key="sh/probe", backend="local"))
    deadline = time.monotonic() + 10
    while not record.is_terminal and time.monotonic() < deadline:
        time.sleep(0.02)
        record = launcher.get(record.id)
    assert record.status is JobStatus.DONE
    assert record.exit_code == 0


# --- a job's files are private ---


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_launched_job_is_private(wired):
    launcher, _, store = wired
    record = launcher.launch(_request(values={"token": "s3cret"}))
    assert _mode(store.job_dir(record.id)) == 0o700
    for path in (
        store.config_path(record.id),
        store.request_path(record.id),
        store.log_path(record.id),
    ):
        assert _mode(path) == 0o600, path.name


def test_a_restart_makes_an_old_config_private(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request(values={"x": 1}))
    store.config_path(record.id).chmod(0o644)  # written by an older typantic
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    launcher.get(record.id)
    launcher.restart(record.id, _request(values={"x": 2}))
    assert _mode(store.config_path(record.id)) == 0o600


# --- a slow poll never overwrites what happened while it ran ---


def _finish(store, record):
    store.save(record.model_copy(update={"status": JobStatus.DONE, "exit_code": 0}))


def test_a_slow_poll_does_not_undo_a_restart(wired):
    # The poll answered about the old run; storing it over the restarted row
    # lost the new run's handle, leaving it running untracked.
    launcher, backend, store = wired
    record = launcher.launch(_request())

    def finish_and_restart(polled):
        backend.on_poll = None
        _finish(store, polled)
        launcher.restart(polled.id)
        return PollResult(status=JobStatus.FAILED, exit_code=1)

    backend.on_poll = finish_and_restart
    launcher.refresh(record)
    stored = store.load(record.id)
    assert stored.status is JobStatus.RUNNING
    assert stored.pid == 4322  # the restarted run's process


def test_a_slow_poll_does_not_turn_a_cancel_into_a_failure(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request())

    def cancel_meanwhile(polled):
        backend.on_poll = None
        launcher.cancel(polled.id)
        return PollResult(status=JobStatus.FAILED)  # the SIGTERM killed it

    backend.on_poll = cancel_meanwhile
    launcher.refresh(record)
    assert store.load(record.id).status is JobStatus.CANCELLED


def test_a_slow_poll_does_not_resurrect_a_deleted_job(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request())

    def delete_meanwhile(polled):
        backend.on_poll = None
        launcher.delete(polled.id)
        return PollResult(status=JobStatus.DONE, exit_code=0)

    backend.on_poll = delete_meanwhile
    launcher.refresh(record)
    assert store.load(record.id) is None


def test_a_poll_of_the_old_run_is_not_reused_for_the_new_one(wired):
    # The cached answer about the old run outlived the restart, and the next
    # refresh within the TTL applied it to the new run.
    launcher, backend, store = wired
    record = launcher.launch(_request())

    def finish_and_restart(polled):
        backend.on_poll = None
        _finish(store, polled)
        launcher.restart(polled.id)
        return PollResult(status=JobStatus.DONE, exit_code=0)

    backend.on_poll = finish_and_restart
    launcher.refresh(record)
    backend.poll_result = PollResult(status=JobStatus.RUNNING)
    assert launcher.get(record.id).status is JobStatus.RUNNING


def test_cancel_does_not_trust_a_cached_running_status(wired):
    # A 2 s old poll said RUNNING for a job that had finished since, so cancel
    # signalled it and recorded CANCELLED over its real outcome.
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    launcher.refresh(record)  # caches RUNNING
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    assert launcher.cancel(record.id).status is JobStatus.DONE
    assert backend.cancelled == []


def test_a_refresh_that_changes_nothing_new_stores_nothing(wired):
    # Another poll already stored this answer; the row is returned as stored.
    launcher, backend, store = wired
    backend.next_status = JobStatus.QUEUED
    record = launcher.launch(_request())

    def start_meanwhile(polled):
        backend.on_poll = None
        store.save(polled.model_copy(update={"status": JobStatus.RUNNING}))
        return PollResult(status=JobStatus.RUNNING)

    backend.on_poll = start_meanwhile
    assert launcher.refresh(record).status is JobStatus.RUNNING


# --- delete asks for the live status before it signals anything ---


def test_delete_does_not_cancel_a_job_that_has_finished(wired):
    # The stored row still said RUNNING; cancelling it signalled a pid that may
    # name an unrelated process by now.
    launcher, backend, _ = wired
    record = launcher.launch(_request())
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    assert launcher.delete(record.id) is True
    assert backend.cancelled == []


def test_delete_project_does_not_cancel_a_job_that_has_finished(wired):
    launcher, backend, store = wired
    project = store.create_project("P")
    launcher.launch(_request(project_id=project.id))
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    assert launcher.delete_project(project.id) is True
    assert backend.cancelled == []


# --- a restart that fails leaves the job exactly as it was ---


def _finished(launcher, backend, **request):
    record = launcher.launch(_request(**request))
    backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
    return launcher.get(record.id)


def _files(store, job_id):
    return {
        name: path.read_text()
        for name, path in (
            ("config", store.config_path(job_id)),
            ("request", store.request_path(job_id)),
            ("log", store.log_path(job_id)),
        )
    }


def test_a_restart_the_backend_rejects_leaves_the_job_as_it_was(wired):
    # The new settings were written before the backend looked at its options,
    # so a rejected restart destroyed the job's stored settings.
    launcher, backend, store = wired
    done = _finished(launcher, backend, values={"x": 1})
    before = _files(store, done.id)
    backend.rejects = ValueError("partition: bad value")
    with pytest.raises(ValueError, match="partition"):
        launcher.restart(done.id, _request(values={"x": 2}))
    assert _files(store, done.id) == before
    assert store.load(done.id) == done


def test_a_restart_that_fails_to_start_puts_the_files_back(wired, monkeypatch):
    launcher, backend, store = wired
    done = _finished(launcher, backend, values={"x": 1})
    before = _files(store, done.id)

    def refuse(*_args, **_kwargs):
        msg = "no scheduler here"
        raise RuntimeError(msg)

    monkeypatch.setattr(backend, "launch", refuse)
    with pytest.raises(RuntimeError):
        launcher.restart(done.id, _request(values={"x": 2}))
    assert _files(store, done.id) == before
    assert store.load(done.id) == done
    assert [p.name for p in store.job_dir(done.id).iterdir()].count("job.log") == 1


def test_a_restarted_job_starts_with_an_empty_log(wired):
    # A queued scheduler job writes nothing until it runs, so the dashboard
    # showed the previous run's log as if it were the new one's.
    launcher, backend, store = wired
    done = _finished(launcher, backend)
    backend.writes_log = False
    launcher.restart(done.id)
    assert store.log_path(done.id).read_text() == ""
    assert sorted(p.name for p in store.job_dir(done.id).iterdir()) == [
        "job.log",
        "launch_request.json",
        "submit_config.json",
    ]


def test_a_restart_runs_from_the_stores_paths(wired):
    # restart wrote the new config to the store's path but launched with the
    # path in the record, which a pre-0.8.0 relative jobs root left relative.
    launcher, backend, store = wired
    done = _finished(launcher, backend)
    stale = done.model_copy(
        update={
            "job_dir": "jobs/x",
            "config_path": "jobs/x/submit_config.json",
            "log_path": "jobs/x/job.log",
        },
    )
    store.save(stale)
    restarted = launcher.restart(done.id)
    argv, _ = backend.launched[-1]
    assert argv[-1] == str(store.config_path(done.id))
    assert restarted.config_path == str(store.config_path(done.id))
    assert restarted.job_dir == str(store.job_dir(done.id))
    assert restarted.log_path == str(store.log_path(done.id))


def test_a_restart_without_its_request_says_what_it_lost(wired, caplog):
    launcher, backend, store = wired
    done = _finished(launcher, backend)
    store.request_path(done.id).unlink()
    launcher.restart(done.id)
    assert "backend options" in caplog.text


# --- a job that started but could not be recorded is stopped ---


def _failing_save(store, monkeypatch):
    real = store.save

    def save(record):
        if record.pid is not None:  # the row that records a started run
            msg = "database is locked"
            raise sqlite3.OperationalError(msg)
        real(record)

    monkeypatch.setattr(store, "save", save)


def test_a_launch_that_cannot_be_recorded_stops_the_job(wired, monkeypatch):
    # Unrecorded, the running job could never be found, cancelled or cleaned up.
    launcher, backend, store = wired
    _failing_save(store, monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        launcher.launch(_request())
    assert len(backend.cancelled) == 1
    assert [p for p in store.root.iterdir() if p.is_dir()] == []


def test_a_restart_that_cannot_be_recorded_stops_the_new_run(wired, monkeypatch):
    launcher, backend, store = wired
    done = _finished(launcher, backend, values={"x": 1})
    before = _files(store, done.id)
    _failing_save(store, monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        launcher.restart(done.id, _request(values={"x": 2}))
    assert backend.cancelled == [done.id]
    assert _files(store, done.id) == before


# --- a submission that timed out may have queued the job ---


def test_a_launch_that_may_have_queued_keeps_its_folder(wired):
    # The job may run: deleting the folder its --output and --chdir name
    # would make it fail on start, with nothing left to say why.
    launcher, backend, store = wired
    backend.rejects = LaunchUncertainError("sbatch did not answer")
    with pytest.raises(LaunchUncertainError):
        launcher.launch(_request())
    (folder,) = [p for p in store.root.iterdir() if p.is_dir()]
    assert (folder / "submit_config.json").exists()


def test_a_restart_that_may_have_queued_keeps_the_new_settings(wired, monkeypatch):
    launcher, backend, store = wired
    done = _finished(launcher, backend, values={"x": 1})

    def uncertain(*_args, **_kwargs):
        msg = "sbatch did not answer"
        raise LaunchUncertainError(msg)

    monkeypatch.setattr(backend, "launch", uncertain)
    with pytest.raises(LaunchUncertainError):
        launcher.restart(done.id, _request(values={"x": 2}))
    assert json.loads(store.config_path(done.id).read_text()) == {"x": 2}


def test_a_failed_restart_removes_what_it_added(wired, monkeypatch):
    launcher, backend, store = wired
    done = _finished(launcher, backend)
    store.request_path(done.id).unlink()
    store.log_path(done.id).unlink()

    def refuse(*_args, **_kwargs):
        msg = "no scheduler here"
        raise RuntimeError(msg)

    monkeypatch.setattr(backend, "launch", refuse)
    with pytest.raises(RuntimeError):
        launcher.restart(done.id, _request(values={"x": 2}))
    assert not store.request_path(done.id).exists()
    assert not store.log_path(done.id).exists()


def test_a_restart_of_a_job_without_a_log(wired):
    launcher, backend, store = wired
    done = _finished(launcher, backend)
    store.log_path(done.id).unlink()
    launcher.restart(done.id)
    assert store.log_path(done.id).read_text() == "hello\n"


# --- a cancel the backend could not carry out ---


def test_a_failed_cancel_is_reported_and_the_job_left_running(wired):
    launcher, backend, store = wired
    record = launcher.launch(_request())
    backend.cancel_error = SchedulerError("Cancel failed (exit 1): no controller")
    with pytest.raises(SchedulerError):
        launcher.cancel(record.id)
    assert store.load(record.id).status is JobStatus.RUNNING


def test_a_cancel_that_fails_because_the_job_just_finished_is_no_error(wired):
    launcher, backend, _ = wired
    record = launcher.launch(_request())

    def finish_then_refuse(_record):
        backend.poll_result = PollResult(status=JobStatus.DONE, exit_code=0)
        msg = "Cancel failed (exit 1): Job/step already completing or completed"
        raise SchedulerError(msg)

    backend.cancel = finish_then_refuse
    assert launcher.cancel(record.id).status is JobStatus.DONE


def test_delete_goes_ahead_when_the_cancel_fails(wired, caplog):
    launcher, backend, store = wired
    record = launcher.launch(_request())
    backend.cancel_error = SchedulerError("Cancel failed (exit 1): no controller")
    assert launcher.delete(record.id) is True
    assert store.load(record.id) is None
    assert "may still be running" in caplog.text
