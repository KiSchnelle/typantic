"""Slurm backend: submit via ``sbatch``, track via ``sacct`` / ``scancel``."""

from pathlib import Path

from typantic.web.backends.base import PollResult
from typantic.web.backends.scheduler import (
    SchedulerBackend,
    SchedulerParams,
    first_nonempty_line,
)
from typantic.web.models import JobStatus

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

    def _directives(
        self,
        params: SchedulerParams,
        *,
        job_dir: Path,
        log_path: Path,
    ) -> list[str]:
        lines = [
            f"#SBATCH --job-name={job_dir.name}",
            f"#SBATCH --output={log_path}",
            # Run in the job folder (parity with the local backend's cwd).
            f"#SBATCH --chdir={job_dir}",
        ]
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

    def _parse_status(self, stdout: str) -> PollResult:
        line = first_nonempty_line(stdout)
        if line is None:
            # Not yet in the accounting DB (just submitted): still queued.
            return PollResult(status=JobStatus.QUEUED)
        state, _, exit_field = line.partition("|")
        return PollResult(
            status=_map_state(state.strip()),
            exit_code=_parse_exit_code(exit_field),
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
    # sacct ExitCode is "<code>:<signal>".
    code = field.strip().split(":", 1)[0]
    try:
        return int(code)
    except ValueError:
        return None
