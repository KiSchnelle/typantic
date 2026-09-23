"""Slurm backend: submit via ``sbatch``, track via ``sacct`` / ``squeue``.

``sacct`` reads the accounting database, which a cluster may not run; ``squeue``
knows a job while it is queued or running, and for a few minutes after it ends.
"""

from pathlib import Path

from typantic.web.backends.base import PollResult
from typantic.web.backends.scheduler import (
    GONE,
    SchedulerBackend,
    SchedulerParams,
    _Gone,
    _log_failed_query,
    _run_tool,
    first_nonempty_line,
)
from typantic.web.models import TERMINAL_STATUSES, JobStatus

# sacct State -> our normalised status. Unrecognised states fall through to
# QUEUED rather than being guessed terminal.
_RUNNING_STATES = frozenset({"RUNNING", "COMPLETING", "SIGNALING", "STAGE_OUT"})
_DONE_STATES = frozenset({"COMPLETED"})
_CANCELLED_STATES = frozenset({"CANCELLED"})
_FAILED_STATES = frozenset(
    {
        "FAILED",
        "TIMEOUT",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "BOOT_FAIL",
        "DEADLINE",
        "PREEMPTED",
        "REVOKED",
    },
)


class SlurmBackend(SchedulerBackend):
    """Submit and track jobs on a Slurm cluster."""

    def _control_args(self, *, job_dir: Path, log_path: Path) -> list[str]:
        return [
            f"--job-name={job_dir.name}",
            f"--output={log_path}",
            # Run in the job folder (parity with the local backend's cwd).
            f"--chdir={job_dir}",
        ]

    def _directives(
        self,
        params: SchedulerParams,
        *,
        job_dir: Path,  # noqa: ARG002 - named on the command line instead
        log_path: Path,  # noqa: ARG002 - named on the command line instead
    ) -> list[str]:
        lines: list[str] = []
        if params.partition:
            lines.append(f"#SBATCH --partition={params.partition}")
        if params.gpus:  # 0/unset means no GPU request -> no gres line
            lines.append(f"#SBATCH --gres=gpu:{params.gpus}")
        if params.cpus is not None:
            lines.append(f"#SBATCH --cpus-per-task={params.cpus}")
        if params.mem:
            lines.append(f"#SBATCH --mem={params.mem}")
        if params.time_minutes:
            lines.append(f"#SBATCH --time={params.time_minutes}")
        lines.extend(f"#SBATCH {extra}" for extra in params.extra)
        return lines

    def _submit_command(self, script_path: Path) -> list[str]:
        return ["sbatch", "--parsable", str(script_path)]

    def _parse_submit(self, stdout: str) -> str:
        # --parsable prints "<jobid>" or "<jobid>;<cluster>".
        return stdout.strip().split(";", 1)[0]

    def _status_command(self, job_id: str) -> list[str]:
        return [
            "sacct",
            "-j",
            job_id,
            "--format=State,ExitCode",
            "--noheader",
            "--parsable2",
        ]

    def _query(self, job_id: str) -> PollResult | _Gone | None:
        result = _run_tool(self._run, self._status_command(job_id))
        if result.returncode == 0 and first_nonempty_line(result.stdout) is not None:
            return self._parse_status(result.stdout)
        # Not in accounting: just submitted, or accounting is off.
        queue = _run_tool(
            self._run,
            ["squeue", "-j", job_id, "--noheader", "--states=all", "--format=%T"],
        )
        if queue.returncode == 0:
            state = first_nonempty_line(queue.stdout)
            if state is None:
                return GONE
            return PollResult(status=_map_state(state.strip()))
        if "Invalid job id" in queue.stderr:
            return GONE
        _log_failed_query(job_id, queue)
        return None

    def _parse_status(self, stdout: str) -> PollResult:
        line = first_nonempty_line(stdout) or ""
        state, _, exit_field = line.partition("|")
        status = _map_state(state.strip())
        # sacct prints "0:0" for a job that has not finished; reporting that as
        # exit_code=0 would render a live job as "exit 0" in the dashboard.
        terminal = status in TERMINAL_STATUSES
        return PollResult(
            status=status,
            exit_code=_parse_exit_code(exit_field) if terminal else None,
        )

    def _cancel_command(self, job_id: str) -> list[str]:
        return ["scancel", job_id]


def _map_state(state: str) -> JobStatus:
    # sacct can suffix states, e.g. "CANCELLED by 1001".
    head = state.split(" ", 1)[0].upper()
    if head in _DONE_STATES:
        return JobStatus.DONE
    if head in _CANCELLED_STATES:
        return JobStatus.CANCELLED
    if head in _FAILED_STATES:
        return JobStatus.FAILED
    if head in _RUNNING_STATES:
        return JobStatus.RUNNING
    # PENDING/CONFIGURING and any unrecognised state: not yet verifiably active.
    return JobStatus.QUEUED


def _parse_exit_code(field: str) -> int | None:
    """The ``<code>:<signal>`` of sacct as one code: 128 + signal if killed."""
    code, _, signal = field.strip().partition(":")
    try:
        exit_code = int(code)
        killed_by = int(signal or 0)
    except ValueError:
        return None
    return 128 + killed_by if killed_by and not exit_code else exit_code
