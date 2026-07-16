import sqlite3
from datetime import UTC, datetime

import pytest

from typantic.web import store as store_mod
from typantic.web.models import JobRecord, JobStatus
from typantic.web.store import JobStore, default_jobs_dir


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def _record(job_id="j1", *, project_id=None, status=JobStatus.RUNNING, created_at=None):
    return JobRecord(
        id=job_id,
        command_key="app/run",
        app="app",
        command="run",
        title="Run",
        project_id=project_id,
        backend="local",
        job_dir=f"/jobs/{job_id}",
        config_path=f"/jobs/{job_id}/submit_config.json",
        log_path=f"/jobs/{job_id}/job.log",
        status=status,
        created_at=created_at or datetime.now(UTC),
    )


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def test_default_jobs_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPANTIC_WEB_JOBS_DIR", str(tmp_path / "custom"))
    assert default_jobs_dir() == tmp_path / "custom"


def test_default_jobs_dir_home(monkeypatch):
    monkeypatch.delenv("TYPANTIC_WEB_JOBS_DIR", raising=False)
    assert default_jobs_dir().name == "jobs"
    assert default_jobs_dir().parent.name == ".typantic"


def test_init_creates_root_and_db(tmp_path):
    root = tmp_path / "jobs"
    JobStore(root)
    assert root.is_dir()
    assert (root / "index.sqlite3").exists()


def test_job_folder_paths(store):
    path = store.create_job_dir("j1")
    assert path.is_dir()
    assert store.job_dir("j1") == path
    assert store.config_path("j1") == path / "submit_config.json"
    assert store.request_path("j1") == path / "launch_request.json"
    assert store.log_path("j1") == path / "job.log"


def test_save_load_roundtrip(store):
    record = _record()
    store.save(record)
    loaded = store.load("j1")
    assert loaded == record


def test_save_is_upsert(store):
    store.save(_record(status=JobStatus.RUNNING))
    store.save(_record(status=JobStatus.DONE))
    loaded = store.load("j1")
    assert loaded is not None
    assert loaded.status is JobStatus.DONE
    assert len(store.list_records()) == 1


def test_load_missing_returns_none(store):
    assert store.load("nope") is None


def test_list_records_newest_first(store):
    store.save(_record("a", created_at=_dt(1)))
    store.save(_record("c", created_at=_dt(3)))
    store.save(_record("b", created_at=_dt(2)))
    assert [r.id for r in store.list_records()] == ["c", "b", "a"]


def _insert_bad_row(store, job_id):
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, command_key, app, command, title, backend, "
            "status, created_at, record_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "a/b", "a", "b", "T", "local", "running", _dt(1).isoformat(),
             "not-json"),
        )


def test_load_malformed_record_returns_none(store):
    _insert_bad_row(store, "bad")
    assert store.load("bad") is None


def test_list_records_skips_malformed(store):
    store.save(_record("good"))
    _insert_bad_row(store, "bad")
    assert [r.id for r in store.list_records()] == ["good"]


def test_delete_existing_job(store):
    store.create_job_dir("j1")
    store.save(_record())
    assert store.delete("j1") is True
    assert store.load("j1") is None
    assert not store.job_dir("j1").exists()


def test_delete_missing_job(store):
    assert store.delete("nope") is False


def test_delete_row_without_folder(store):
    store.save(_record())  # no folder created
    assert store.delete("j1") is True


def test_delete_folder_without_row(store):
    store.create_job_dir("j1")  # folder only, no DB row
    assert store.delete("j1") is True


def test_projects_crud(store):
    proj = store.create_project("Screen A", "desc")
    assert store.get_project(proj.id) == proj
    assert [p.id for p in store.list_projects()] == [proj.id]
    assert store.get_project("missing") is None
    assert store.delete_project(proj.id) is True
    assert store.delete_project(proj.id) is False


def test_delete_project_unfiles_jobs(store):
    proj = store.create_project("P")
    store.save(_record("j1", project_id=proj.id))
    store.delete_project(proj.id)
    reloaded = store.load("j1")
    assert reloaded is not None
    assert reloaded.project_id is None


def test_save_with_unknown_project_raises(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.save(_record(project_id="ghost"))


def test_grouped_history(store):
    proj = store.create_project("P")
    empty = store.create_project("Empty")
    store.save(_record("j1", project_id=proj.id, created_at=_dt(1)))
    store.save(_record("j2", project_id=proj.id, created_at=_dt(3)))
    store.save(_record("solo", created_at=_dt(2)))

    history = store.grouped_history()
    groups = {g.project.id: [j.id for j in g.jobs] for g in history.projects}
    assert groups[proj.id] == ["j2", "j1"]  # newest first
    assert groups[empty.id] == []
    assert [j.id for j in history.ungrouped] == ["solo"]


def test_grouped_history_empty(store):
    history = store.grouped_history()
    assert history.projects == []
    assert history.ungrouped == []


def test_grouped_history_skips_malformed(store):
    store.save(_record("good"))
    _insert_bad_row(store, "bad")
    history = store.grouped_history()
    assert [j.id for j in history.ungrouped] == ["good"]


def test_logger_name():
    assert store_mod.logger.name == "typantic.web"
