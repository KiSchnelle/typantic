"""The backend contract and its small result types.

A backend turns a built argv into a tracked job. The launcher is
backend-agnostic: it always writes the job's log itself (subprocess capture, or
a scheduler ``--output``) so tailing works the same everywhere, and it never
branches on backend identity — each backend reports its own initial
:class:`~typantic.web.models.JobStatus` via :attr:`Launched.status`.
"""

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from typantic.web.models import JobRecord, JobStatus


class Launched(BaseModel):
    """What a backend returns after starting a job.

    ``status`` is the job's initial state (a local process is RUNNING at once; a
    scheduler job is QUEUED). Exactly one handle (``pid`` or ``scheduler_id``) is
    set, depending on the backend family.
    """

    status: JobStatus
    pid: int | None = None
    scheduler_id: str | None = None


class PollResult(BaseModel):
    """A backend's status readout for a job.

    Attributes:
        status: The normalised status.
        exit_code: The process/job exit code once finished, else ``None``.
    """

    status: JobStatus
    exit_code: int | None = None


class LaunchBackend(Protocol):
    """Start, poll, and cancel a job."""

    def launch(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> Launched:
        """Start ``argv`` as a tracked job, writing its output to ``log_path``.

        Args:
            argv: The full command to run (``<app> <cmd> --config …``).
            job_dir: The per-job working directory.
            log_path: Where the job's combined stdout/stderr must land (so the
                dashboard can tail it identically across backends).
            backend_options: Backend-specific options (validated internally).

        Returns:
            A handle identifying the started job and its initial status.
        """
        ...

    def poll(self, record: JobRecord) -> PollResult:
        """Re-resolve ``record``'s status from the OS / scheduler."""
        ...

    def cancel(self, record: JobRecord) -> None:
        """Stop the job (best effort)."""
        ...

    def preview(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> str:
        """Return the exact command line or submit script this launch would run.

        For a dry-run: process backends return the wrapped shell command;
        scheduler backends return the rendered submit script.
        """
        ...
