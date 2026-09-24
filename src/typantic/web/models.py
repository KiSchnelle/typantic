"""Framework-free data models shared across the launcher, store, and API.

These are plain pydantic models (no FastAPI, no launched-app imports), so the
CLI, the launch backends, and the web API all share one typed surface.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


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


TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})
"""The states a job never leaves (mirrored by the dashboard's own list)."""


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
    launched via ``--config``; ``script`` is what the backend would actually run
    -- a rendered submit script for the schedulers, the wrapped shell command for
    the process family. Every backend renders one, so it is always present.
    """

    config: str
    argv: list[str]
    script: str


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
    pid_start: int | None = None
    scheduler_id: str | None = None
    # The machine a process-family job's pid lives on; None for a scheduler job,
    # and for one recorded before 0.8.0.
    host: str | None = None
    # The version of the app that ran the job (the installed distribution that
    # provides its console script); None when unknown, and for one recorded
    # before 0.8.1.
    app_version: str | None = None
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime
    finished_at: datetime | None = None
    exit_code: int | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the job has reached a final state (no more polling needed)."""
        return self.status in TERMINAL_STATUSES


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


class BackendMeta(BaseModel):
    """One installed backend and the JSON Schema for its options (None = none)."""

    key: str
    options_schema: dict[str, Any] | None = None


_ICON_MAX_BYTES = 64 * 1024
# What may come before an SVG's root element: whitespace, an XML declaration, a
# doctype, and comments.
_SVG_PROLOG = re.compile(r"\s*(?:<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->)", re.DOTALL)


def _is_svg(markup: str) -> bool:
    """Whether ``markup`` is an SVG document: ``<svg`` after any prolog."""
    position = 0
    while match := _SVG_PROLOG.match(markup, position):
        position = match.end()
    return markup[position:].lstrip().startswith("<svg")


class Brand(BaseModel):
    """How the dashboard presents itself: its name, wordmark, mark and accent.

    A package installs one under the ``typantic.web_brand`` entry-point group
    (see :func:`typantic.web.brand.discover_brand`), and ``typantic web serve
    --title/--icon/--accent`` override it for a run. Unknown keys are ignored,
    so a brand written for a later typantic still loads.
    """

    model_config = ConfigDict(extra="ignore")

    title: str = Field(
        default="typantic web",
        min_length=1,
        description="The dashboard's name, in the browser tab.",
    )
    lead: str = Field(
        default="",
        description="The wordmark's bold part; with rest empty too, the title's "
        "first word.",
    )
    rest: str = Field(
        default="",
        description="The wordmark's light part; by default the rest of the title.",
    )
    icon: str | None = Field(
        default=None,
        description="The mark, as SVG markup (not a path): the sidebar logo and the "
        "browser tab's icon. At most 64 KiB.",
    )
    accent: str | None = Field(
        default=None,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="The accent colour, #rrggbb; typantic's cyan by default.",
    )

    @field_validator("icon")
    @classmethod
    def _svg_document(cls, icon: str | None) -> str | None:
        if icon is None:
            return None
        if len(icon.encode()) > _ICON_MAX_BYTES:
            msg = f"the icon is over {_ICON_MAX_BYTES // 1024} KiB"
            raise ValueError(msg)
        if not _is_svg(icon):
            msg = "the icon must be SVG markup, starting <svg"
            raise ValueError(msg)
        return icon

    @model_validator(mode="after")
    def _fill_wordmark(self) -> Self:
        # Split exactly as the dashboard always has: at the title's first space.
        if not self.lead and not self.rest:
            self.lead, _, self.rest = self.title.partition(" ")
        return self


class ApiMeta(BaseModel):
    """The ``/api/meta`` payload: dashboard brand, version, and the backends.

    The brand is flattened into it: ``icon`` is the brand's SVG as a data URI,
    which the page only ever shows in an ``<img>`` or as the tab's icon, where
    an SVG's scripts do not run.
    """

    title: str
    version: str
    backends: list[BackendMeta]
    wordmark_lead: str = ""
    wordmark_rest: str = ""
    icon: str | None = None
    accent: str | None = None


class FsEntry(BaseModel):
    """One entry (file or directory) in a path-picker listing."""

    name: str
    is_dir: bool


class FsListing(BaseModel):
    """A path-picker directory listing, with paging bounds and any read error."""

    path: str
    parent: str | None = None
    entries: list[FsEntry]
    error: str | None = None
    total: int
    truncated: bool


class JobImage(BaseModel):
    """One output image discovered under a job's artifact roots."""

    name: str
    root: int
    url: str


class JobImages(BaseModel):
    """A job's output images, newest first, and whether more exist than listed."""

    images: list[JobImage]
    truncated: bool = False
