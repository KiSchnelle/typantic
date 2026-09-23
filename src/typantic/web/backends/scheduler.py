"""Scheduler backends: submit a job to an HPC batch scheduler, track it by job id.

The command is wrapped in a submit script whose output is directed at the same
per-job log a local job captures to, so the dashboard tails both identically
(shared filesystem). The script also leaves the command's exit code in the job
folder, as a local job does, which tells how a job ended even once the scheduler
has forgotten it (accounting off, job history purged).

``SchedulerBackend`` holds the shared submit / poll / cancel flow; a concrete
scheduler (Slurm, PBS) fills in its directive syntax, its submit/query/cancel
commands, and how it parses their output. The scheduler tools are invoked
through an injectable ``runner`` so backends are testable without a cluster.
"""

import abc
import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web._files import write_private
from typantic.web._subprocess import run_tool
from typantic.web.backends._marker import (
    EXIT_MARKER,
    clear_exit_code,
    read_exit_code,
)
from typantic.web.backends.base import Launched, LaunchUncertainError, PollResult
from typantic.web.models import JobRecord, JobStatus

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

logger = logging.getLogger("typantic.web")

_SUBMIT_SCRIPT = "submit.sh"
_TOOL_TIMEOUT_S = 30
_TOOL_UNAVAILABLE = -1
"""Return code standing in for "the tool could not be run at all"."""
_TOOL_TIMED_OUT = -2
"""Return code standing in for "the tool did not answer in time"."""
_GONE_GRACE_S = 120
"""How long a job the scheduler has forgotten may still leave its exit marker.

A shared filesystem can show the login node a file written on a compute node a
minute or so late (NFS caches the directory listing that said it was missing).
"""


class _Gone:
    """The scheduler no longer knows the job: it has ended, but how is unknown."""


GONE = _Gone()


# Each option becomes a line of the batch script, which runs as the user on the
# cluster: a newline in one would start a shell command of its own.
_ONE_WORD = r"^[^\s#]+$"
_ONE_LINE = r"^[^\r\n]*$"


class SchedulerParams(BaseModel):
    """A batch resource request, shared across schedulers."""

    model_config = ConfigDict(extra="forbid")

    partition: str | None = Field(
        default=None,
        pattern=_ONE_WORD,
        description="Partition / queue.",
    )
    gpus: int | None = Field(default=None, ge=0, description="GPUs to request.")
    cpus: int | None = Field(default=None, ge=1, description="CPUs per task.")
    mem: str | None = Field(
        default=None,
        pattern=_ONE_WORD,
        description="Memory, in the scheduler's syntax: Slurm '16G', PBS '16gb'.",
    )
    time_minutes: int | None = Field(
        default=None,
        ge=1,
        description="Wall-clock limit in minutes.",
    )
    extra: list[Annotated[str, Field(pattern=_ONE_LINE)]] = Field(
        default_factory=list,
        description="Raw extra directive arguments, one per line.",
    )


class SchedulerError(RuntimeError):
    """Raised when a scheduler submission fails."""


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # No stdin, output decoded with replacement (a Latin-1 job name from sacct
    # must not raise), and a timeout kills the tool's whole process group.
    return run_tool(argv, timeout=_TOOL_TIMEOUT_S)


def _run_tool(run: Runner, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a scheduler tool, turning "could not run it" into a failed result.

    A missing binary or a hung tool is a fact about the cluster, not a bug in the
    caller: surfaced as an exception it would escape ``poll`` and 500 the whole
    jobs list, since every listed job is refreshed on read.
    """
    try:
        return run(argv)
    except subprocess.TimeoutExpired as exc:
        logger.warning("Scheduler tool %s timed out: %s", argv[0], exc)
        return subprocess.CompletedProcess(
            argv,
            returncode=_TOOL_TIMED_OUT,
            stdout="",
            stderr=str(exc),
        )
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
        # When each forgotten job was first found gone, by scheduler id.
        self._gone_since: dict[str, float] = {}

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
    def _parse_status(self, stdout: str) -> PollResult | _Gone:
        """Map the status command's stdout to a :class:`PollResult`.

        :data:`GONE` for a job that has ended without an outcome on record.
        """

    @abc.abstractmethod
    def _cancel_command(self, job_id: str) -> list[str]:
        """The command that cancels ``job_id``."""

    def _unknown_job(
        self,
        result: "subprocess.CompletedProcess[str]",  # noqa: ARG002 - subclass hook
    ) -> bool:
        """Whether a failed status query says the scheduler does not know the job."""
        return False

    # --- shared flow ---

    def _control_args(
        self,
        *,
        job_dir: Path,  # noqa: ARG002 - subclass hook
        log_path: Path,  # noqa: ARG002 - subclass hook
    ) -> list[str]:
        """Submit options naming the job, its log and its folder.

        Given on the submit command line, where a path with a space in it is
        still one argument, rather than as directives in the script. Inserted
        right after the tool's name; empty by default, for a scheduler whose
        directives still carry them.
        """
        return []

    def _submit(self, script_path: Path, *, job_dir: Path, log_path: Path) -> list[str]:
        tool, *rest = self._submit_command(script_path)
        return [tool, *self._control_args(job_dir=job_dir, log_path=log_path), *rest]

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
        marker = shlex.quote(str(job_dir / EXIT_MARKER))
        # The script exits with the command's own code, so the scheduler's
        # record of the job still tells a failure apart.
        lines.extend(
            ["", shlex.join(argv), "rc=$?", f'echo "$rc" > {marker}', 'exit "$rc"', ""]
        )
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
        clear_exit_code(job_dir)  # a restart in place must start unfinished
        script_path = job_dir / _SUBMIT_SCRIPT
        write_private(
            script_path,
            self._script(argv, job_dir=job_dir, log_path=log_path, params=params),
        )
        submit = self._submit(script_path, job_dir=job_dir, log_path=log_path)
        result = _run_tool(self._run, submit)
        if result.returncode == _TOOL_TIMED_OUT:
            msg = (
                f"{submit[0]} did not answer within {_TOOL_TIMEOUT_S} s, and the job "
                f"may have been queued anyway: check the scheduler's queue. Its "
                f"folder is kept at {job_dir}."
            )
            raise LaunchUncertainError(msg)
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
        """Resolve status: the job's exit marker first, then the scheduler.

        A job that ran its command to the end left its exit code in its folder,
        which holds whatever the scheduler still remembers. A query that could
        not run (missing tool, timeout, controller down) says nothing about the
        job, so the last known status is kept rather than reading the empty
        output as "not in the queue" -- which would report a dead cluster as
        QUEUED forever. A job the scheduler no longer knows, with no marker,
        ended before its command did (killed, timed out, lost with its node):
        FAILED, once the marker has had time to show up.
        """
        job_id = record.scheduler_id
        if job_id is None:
            return PollResult(status=JobStatus.FAILED)
        code = read_exit_code(Path(record.job_dir))
        if code is not None:
            self._gone_since.pop(job_id, None)
            status = JobStatus.DONE if code == 0 else JobStatus.FAILED
            return PollResult(status=status, exit_code=code)
        state = self._query(job_id)
        if isinstance(state, PollResult):
            self._gone_since.pop(job_id, None)
            return state
        last_known = PollResult(status=record.status, exit_code=record.exit_code)
        if state is None:
            return last_known
        gone_since = self._gone_since.setdefault(job_id, time.monotonic())
        if time.monotonic() - gone_since < _GONE_GRACE_S:
            return last_known
        self._gone_since.pop(job_id, None)
        return PollResult(status=JobStatus.FAILED)

    def _query(self, job_id: str) -> PollResult | _Gone | None:
        """Ask the scheduler: the job's state, :data:`GONE`, or ``None`` (no answer)."""
        result = _run_tool(self._run, self._status_command(job_id))
        if result.returncode == 0:
            return self._parse_status(result.stdout)
        if self._unknown_job(result):
            return GONE
        _log_failed_query(job_id, result)
        return None

    def cancel(self, record: JobRecord) -> None:
        """Cancel the job through the scheduler.

        Raises:
            SchedulerError: If the scheduler refused, or could not be asked. As
                far as anyone knows the job is then still running, and recording
                it cancelled would hide that.
        """
        if record.scheduler_id is None:
            return
        result = _run_tool(self._run, self._cancel_command(record.scheduler_id))
        if result.returncode != 0:
            detail = result.stderr.strip()
            msg = f"Cancel failed (exit {result.returncode}): {detail}"
            raise SchedulerError(msg)

    def preview(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> str:
        """Return the submit command and the script this launch would render."""
        params = SchedulerParams.model_validate(backend_options)
        script = self._script(argv, job_dir=job_dir, log_path=log_path, params=params)
        submit = self._submit(
            job_dir / _SUBMIT_SCRIPT,
            job_dir=job_dir,
            log_path=log_path,
        )
        return f"# Submitted with: {shlex.join(submit)}\n{script}"


def _log_failed_query(job_id: str, result: "subprocess.CompletedProcess[str]") -> None:
    logger.warning(
        "Status query for job %s failed (exit %s): %s",
        job_id,
        result.returncode,
        result.stderr.strip(),
    )


def first_nonempty_line(text: str) -> str | None:
    """Return the first non-blank line of ``text``, or ``None``."""
    for raw in text.splitlines():
        if raw.strip():
            return raw
    return None
