"""Scheduler backends: submit a job to an HPC batch scheduler, track it by job id.

The command is wrapped in a submit script whose output is directed at the same
per-job log a local job captures to, so the dashboard tails both identically
(shared filesystem). ``SchedulerBackend`` holds the shared submit / poll / cancel
flow; a concrete scheduler (Slurm, PBS) fills in its directive syntax, its
submit/query/cancel commands, and how it parses their output. The scheduler
tools are invoked through an injectable ``runner`` so backends are testable
without a cluster.
"""

import abc
import logging
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web.backends.base import Launched, PollResult
from typantic.web.models import JobRecord, JobStatus

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

logger = logging.getLogger("typantic.web")

_SUBMIT_SCRIPT = "submit.sh"
_TOOL_TIMEOUT_S = 30
_TOOL_UNAVAILABLE = -1
"""Return code standing in for "the tool could not be run at all"."""


class SchedulerParams(BaseModel):
    """A batch resource request, shared across schedulers."""

    model_config = ConfigDict(extra="forbid")

    partition: str | None = Field(default=None, description="Partition / queue.")
    gpus: int | None = Field(default=None, ge=0, description="GPUs to request.")
    cpus: int | None = Field(default=None, ge=1, description="CPUs per task.")
    mem: str | None = Field(default=None, description="Memory, e.g. '16G'.")
    time_minutes: int | None = Field(
        default=None,
        ge=1,
        description="Wall-clock limit in minutes.",
    )
    extra: list[str] = Field(
        default_factory=list,
        description="Raw extra directive arguments, one per line.",
    )


class SchedulerError(RuntimeError):
    """Raised when a scheduler submission fails."""


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed scheduler tool names, no shell
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TOOL_TIMEOUT_S,
    )


def _run_tool(run: Runner, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a scheduler tool, turning "could not run it" into a failed result.

    A missing binary or a hung tool is a fact about the cluster, not a bug in the
    caller: surfaced as an exception it would escape ``poll`` and 500 the whole
    jobs list, since every listed job is refreshed on read.
    """
    try:
        return run(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Scheduler tool %s could not be run: %s", argv[0], exc)
        return subprocess.CompletedProcess(
            argv,
            returncode=_TOOL_UNAVAILABLE,
            stdout="",
            stderr=str(exc),
        )


class SchedulerBackend(abc.ABC):
    """Submit and track jobs on a batch scheduler."""

    options_model: ClassVar[type[BaseModel]] = SchedulerParams

    def __init__(self, runner: Runner | None = None) -> None:
        """Create the backend; ``runner`` defaults to the real scheduler tools."""
        self._run: Runner = runner or _default_runner

    # --- scheduler-specific hooks ---

    @abc.abstractmethod
    def _directives(
        self,
        params: SchedulerParams,
        *,
        job_dir: Path,
        log_path: Path,
    ) -> list[str]:
        """Return the directive lines (e.g. ``#SBATCH ...``) for a submit script."""

    @abc.abstractmethod
    def _submit_command(self, script_path: Path) -> list[str]:
        """The command that submits ``script_path``."""

    @abc.abstractmethod
    def _parse_submit(self, stdout: str) -> str:
        """Extract the job id from the submit command's stdout."""

    @abc.abstractmethod
    def _status_command(self, job_id: str) -> list[str]:
        """The command that queries ``job_id``'s status."""

    @abc.abstractmethod
    def _parse_status(self, stdout: str) -> PollResult:
        """Map the status command's stdout to a :class:`PollResult`."""

    @abc.abstractmethod
    def _cancel_command(self, job_id: str) -> list[str]:
        """The command that cancels ``job_id``."""

    # --- shared flow ---

    def _preamble(self, *, job_dir: Path) -> list[str]:  # noqa: ARG002 - subclass hook
        """Shell lines to run after the directives, before the command.

        Empty by default; a scheduler with no "start here" directive uses this to
        ``cd`` into the job folder itself.
        """
        return []

    def _script(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        params: SchedulerParams,
    ) -> str:
        lines = ["#!/bin/bash"]
        # Directives must precede the first non-comment line, so the preamble sits
        # between them and the command.
        lines.extend(self._directives(params, job_dir=job_dir, log_path=log_path))
        lines.extend(self._preamble(job_dir=job_dir))
        lines.extend(["", shlex.join(argv), ""])
        return "\n".join(lines)

    def launch(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> Launched:
        """Render and submit a batch script, returning the scheduler job id."""
        params = SchedulerParams.model_validate(backend_options)
        script_path = job_dir / _SUBMIT_SCRIPT
        script_path.write_text(
            self._script(argv, job_dir=job_dir, log_path=log_path, params=params),
        )
        result = _run_tool(self._run, self._submit_command(script_path))
        if result.returncode != 0:
            detail = result.stderr.strip()
            msg = f"Submission failed (exit {result.returncode}): {detail}"
            raise SchedulerError(msg)
        job_id = self._parse_submit(result.stdout)
        if not job_id:
            msg = "Scheduler did not return a job id."
            raise SchedulerError(msg)
        return Launched(scheduler_id=job_id, status=JobStatus.QUEUED)

    def poll(self, record: JobRecord) -> PollResult:
        """Resolve status by querying the scheduler for this job id.

        A query that could not run (missing tool, timeout, nonzero exit) says
        nothing about the job, so the last known status is kept rather than
        reading the empty output as "not in the queue" -- which would report a
        dead cluster as QUEUED forever.
        """
        if record.scheduler_id is None:
            return PollResult(status=JobStatus.FAILED)
        result = _run_tool(self._run, self._status_command(record.scheduler_id))
        if result.returncode != 0:
            logger.warning(
                "Status query for job %s failed (exit %s): %s",
                record.scheduler_id,
                result.returncode,
                result.stderr.strip(),
            )
            return PollResult(status=record.status, exit_code=record.exit_code)
        return self._parse_status(result.stdout)

    def cancel(self, record: JobRecord) -> None:
        """Cancel the job through the scheduler (best effort)."""
        if record.scheduler_id is not None:
            _run_tool(self._run, self._cancel_command(record.scheduler_id))

    def preview(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> str:
        """Return the submit script this launch would render."""
        params = SchedulerParams.model_validate(backend_options)
        return self._script(argv, job_dir=job_dir, log_path=log_path, params=params)


def first_nonempty_line(text: str) -> str | None:
    """Return the first non-blank line of ``text``, or ``None``."""
    for raw in text.splitlines():
        if raw.strip():
            return raw
    return None
