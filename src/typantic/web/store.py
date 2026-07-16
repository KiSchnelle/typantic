"""Per-user job store: a SQLite index over one folder per job.

Each job owns a folder under ``~/.typantic/jobs/<job_id>/`` holding its
artifacts — the submitted config, the launch request, and the captured log.
Job and project *metadata* live in a single SQLite database
(``index.sqlite3``) in the same root, which is the authoritative record and the
thing history/project queries run against. The stored ``record_json`` column is
a lossless copy of each :class:`JobRecord`, so column drift never loses data.
"""

import contextlib
import logging
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from typantic.web.models import (
    History,
    JobRecord,
    Project,
    ProjectGroup,
)

_LOG_FILE = "job.log"
_CONFIG_FILE = "submit_config.json"
_REQUEST_FILE = "launch_request.json"
_DB_FILE = "index.sqlite3"
_ENV_JOBS_DIR = "TYPANTIC_WEB_JOBS_DIR"

logger = logging.getLogger("typantic.web")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE SET NULL,
    command_key TEXT NOT NULL,
    app         TEXT NOT NULL,
    command     TEXT NOT NULL,
    title       TEXT NOT NULL,
    name        TEXT,
    backend     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    exit_code   INTEGER,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

_JOB_COLUMNS = (
    "id",
    "project_id",
    "command_key",
    "app",
    "command",
    "title",
    "name",
    "backend",
    "status",
    "created_at",
    "finished_at",
    "exit_code",
    "record_json",
)


def default_jobs_dir() -> Path:
    """The per-user jobs root (``$TYPANTIC_WEB_JOBS_DIR`` or ``~/.typantic/jobs``)."""
    override = os.environ.get(_ENV_JOBS_DIR)
    if override:
        return Path(override)
    return Path.home() / ".typantic" / "jobs"


class JobStore:
    """Create and enumerate job folders; index their metadata + projects in SQLite."""

    def __init__(self, root: Path | None = None) -> None:
        """Open (creating if needed) the store rooted at ``root``."""
        self.root = root or default_jobs_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.root / _DB_FILE
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a fresh connection, committing on success and always closing."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- job folders (artifacts) ---

    def job_dir(self, job_id: str) -> Path:
        """The folder for ``job_id`` (not guaranteed to exist)."""
        return self.root / job_id

    def create_job_dir(self, job_id: str) -> Path:
        """Create and return a fresh folder for ``job_id``."""
        path = self.job_dir(job_id)
        path.mkdir(parents=True, exist_ok=False)
        return path

    def config_path(self, job_id: str) -> Path:
        """Where a job's raw submitted config is written."""
        return self.job_dir(job_id) / _CONFIG_FILE

    def request_path(self, job_id: str) -> Path:
        """Where a job's full launch request is written (for clone/restart)."""
        return self.job_dir(job_id) / _REQUEST_FILE

    def log_path(self, job_id: str) -> Path:
        """Where a job's combined stdout/stderr is captured."""
        return self.job_dir(job_id) / _LOG_FILE

    # --- job metadata (SQLite) ---

    def save(self, record: JobRecord) -> None:
        """Insert or update a job's metadata row from ``record``."""
        placeholders = ", ".join("?" for _ in _JOB_COLUMNS)
        columns = ", ".join(_JOB_COLUMNS)
        values = (
            record.id,
            record.project_id,
            record.command_key,
            record.app,
            record.command,
            record.title,
            record.name,
            record.backend,
            record.status.value,
            record.created_at.isoformat(),
            record.finished_at.isoformat() if record.finished_at else None,
            record.exit_code,
            record.model_dump_json(),
        )
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO jobs ({columns}) VALUES ({placeholders})",  # noqa: S608
                values,
            )

    def load(self, job_id: str) -> JobRecord | None:
        """Load a job record, or ``None`` if missing / unreadable."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id, record_json FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _record_from_row(row)

    def list_records(self) -> list[JobRecord]:
        """All job records, newest first (by creation time)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, record_json FROM jobs ORDER BY created_at DESC",
            ).fetchall()
        return [record for row in rows if (record := _record_from_row(row)) is not None]

    def delete(self, job_id: str) -> bool:
        """Remove a job's folder and metadata row.

        Returns ``True`` if the job existed. Only the store folder (log, config,
        request) is removed; an output folder the user pointed elsewhere is their
        own data and is not this store's to touch.
        """
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            existed = cursor.rowcount > 0
        directory = self.job_dir(job_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            existed = True
        return existed

    # --- projects ---

    def create_project(self, name: str, description: str = "") -> Project:
        """Create and persist a new project."""
        project = Project(
            id=uuid.uuid4().hex,
            name=name,
            description=description,
            created_at=datetime.now(UTC),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, description, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.description,
                    project.created_at.isoformat(),
                ),
            )
        return project

    def list_projects(self) -> list[Project]:
        """All projects, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at FROM projects "
                "ORDER BY created_at DESC",
            ).fetchall()
        return [Project.model_validate(dict(row)) for row in rows]

    def get_project(self, project_id: str) -> Project | None:
        """Return a project by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, description, created_at FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        return Project.model_validate(dict(row)) if row is not None else None

    def delete_project(self, project_id: str) -> bool:
        """Delete a project; member jobs are un-filed (``project_id`` set NULL)."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cursor.rowcount > 0

    def grouped_history(self) -> History:
        """Return job history: jobs grouped by project, plus ungrouped singles."""
        projects = self.list_projects()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, record_json FROM jobs ORDER BY created_at DESC",
            ).fetchall()
        by_project: dict[str, list[JobRecord]] = {}
        ungrouped: list[JobRecord] = []
        for row in rows:
            record = _record_from_row(row)
            if record is None:
                continue
            if row["project_id"] is None:
                ungrouped.append(record)
            else:
                by_project.setdefault(row["project_id"], []).append(record)
        groups = [
            ProjectGroup(project=project, jobs=by_project.get(project.id, []))
            for project in projects
        ]
        return History(projects=groups, ungrouped=ungrouped)


def _record_from_row(row: sqlite3.Row | None) -> JobRecord | None:
    """Parse a job row into a :class:`JobRecord`, tolerating junk.

    The flat ``project_id`` column is authoritative for project membership (the
    ``ON DELETE SET NULL`` foreign key maintains it), so it overrides whatever
    the ``record_json`` blob carried.
    """
    if row is None:
        return None
    try:
        record = JobRecord.model_validate_json(row["record_json"])
    except ValidationError:
        logger.warning("Ignoring malformed job record in the store index.")
        return None
    return record.model_copy(update={"project_id": row["project_id"]})
