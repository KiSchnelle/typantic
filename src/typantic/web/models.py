"""Framework-free data models shared across the launcher, store, and API.

These are plain pydantic models (no FastAPI, no launched-app imports), so the
CLI, the launch backends, and the web API all share one typed surface.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class CommandMeta(BaseModel):
    """One launchable command, discovered from an app's ``web_meta``.

    Built by validating the plain mappings an app registers under the
    ``typantic.web_commands`` entry-point group. Unknown keys are ignored so a
    newer app may add fields without breaking an older gateway.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    app: str = Field(description="Console-script executable, e.g. 'myapp'.")
    command: str = Field(description="Unique command id within the app.")
    argv: tuple[str, ...] = Field(
        description="Tokens after the executable selecting the command.",
    )
    title: str = Field(description="Human label for the catalog.")
    description: str = Field(default="", description="One-line help.")
    default_backend: str = Field(
        default="local",
        description="Pre-selected launch backend key for this command.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        """A globally-unique id across apps (``app/command``)."""
        return f"{self.app}/{self.command}"

    def invocation(self, *extra: str) -> list[str]:
        """Return the full argv to invoke this command, with ``extra`` appended.

        E.g. ``meta.invocation("--schema")`` or
        ``meta.invocation("--config", str(path))``.
        """
        return [self.app, *self.argv, *extra]


class JobStatus(StrEnum):
    """Normalised lifecycle state, unified across every backend.

    Attributes:
        QUEUED: Submitted, not yet running (e.g. a pending scheduler job).
        RUNNING: The process / job is executing.
        DONE: Finished with exit code 0.
        FAILED: Finished non-zero, was killed, or the process vanished.
        CANCELLED: Cancelled by the user.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


class LaunchRequest(BaseModel):
    """A form submission asking to launch one command."""

    model_config = ConfigDict(extra="forbid")

    command_key: str = Field(description="The CommandMeta key, 'app/command'.")
    backend: str = Field(description="Backend key to run on, e.g. 'local' or 'slurm'.")
    name: str | None = Field(
        default=None,
        description="Optional human label shown in the jobs list.",
    )
    project_id: str | None = Field(
        default=None,
        description="Optional project to file this job under.",
    )
    values: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw form values; written to submit_config.json and launched "
        "via --config (the CLI does the authoritative validation).",
    )
    backend_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-specific options; each backend validates its own "
        "(e.g. slurm resources, a container image). Ignored by backends that "
        "take none.",
    )


class MakeDirRequest(BaseModel):
    """A request to create one new folder under ``path`` for the path picker."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Existing parent directory to create under.")
    name: str = Field(description="Name of the single new folder (no separators).")


class LaunchPreview(BaseModel):
    """A dry-run of a launch: the config that would be written, and the argv/script.

    ``config`` is exactly the ``submit_config.json`` content that would be
    launched via ``--config``; ``script`` is a rendered submit wrapper, if the
    backend uses one (schedulers), else ``None``.
    """

    config: str
    argv: list[str]
    script: str | None = None


class Project(BaseModel):
    """A named grouping of jobs, for organising and querying history."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str = ""
    created_at: datetime


class ProjectCreate(BaseModel):
    """A request to create a new project."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Project name.")
    description: str = Field(default="", description="Optional description.")


class JobRecord(BaseModel):
    """The durable record of one launched job.

    The on-disk job folder holds the artifacts (config, log); this record's
    metadata is the DB's authoritative copy, and its status is re-resolved from
    the backend.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    command_key: str
    app: str
    command: str
    title: str
    name: str | None = None
    project_id: str | None = None
    backend: str
    job_dir: str
    config_path: str
    log_path: str
    pid: int | None = None
    scheduler_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime
    finished_at: datetime | None = None
    exit_code: int | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the job has reached a final state (no more polling needed)."""
        return self.status in _TERMINAL_STATUSES


class JobPage(BaseModel):
    """A page of jobs plus the total number matching the query."""

    jobs: list[JobRecord]
    total: int


class ProjectGroup(BaseModel):
    """A project together with its jobs, newest first."""

    project: Project
    jobs: list[JobRecord]


class History(BaseModel):
    """Job history: jobs grouped by project, plus ungrouped single jobs."""

    projects: list[ProjectGroup]
    ungrouped: list[JobRecord]
