"""PBS backend: submit via ``qsub``, track via ``qstat -x`` / ``qdel``.

Targets PBS Pro (``qstat -x`` keeps finished jobs in the listing). The submit
script mirrors the Slurm backend's, translated to ``#PBS`` directives.
"""

import shlex
import subprocess
from pathlib import Path

from typantic.web.backends.base import PollResult
from typantic.web.backends.scheduler import (
    GONE,
    SchedulerBackend,
    SchedulerParams,
    _Gone,
)
from typantic.web.models import JobStatus

# PBS job_state letters we treat as running (E = exiting/epilogue).
_RUNNING_STATES = frozenset({"R", "E"})
# Terminal letters (C = Torque complete, F = PBS Pro finished).
_FINISHED_STATES = frozenset({"C", "F"})


class PbsBackend(SchedulerBackend):
    """Submit and track jobs on a PBS cluster."""

    def _control_args(self, *, job_dir: Path, log_path: Path) -> list[str]:
        return [
            "-N",
            _job_name(job_dir.name),
            "-o",
            str(log_path),
            "-j",
            "oe",  # merge stderr into the -o log
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
            lines.append(f"#PBS -q {params.partition}")
        resources = []
        if params.cpus is not None:
            resources.append(f"ncpus={params.cpus}")
        if params.gpus:
            resources.append(f"ngpus={params.gpus}")
        if params.mem:
            resources.append(f"mem={params.mem}")
        if resources:
            lines.append(f"#PBS -l select=1:{':'.join(resources)}")
        if params.time_minutes:
            lines.append(f"#PBS -l walltime={_walltime(params.time_minutes)}")
        lines.extend(f"#PBS {extra}" for extra in params.extra)
        return lines

    def _preamble(self, *, job_dir: Path) -> list[str]:
        # PBS starts a job in the user's home, so a command's relative output
        # would land there instead of the job folder the gallery scans. There is
        # no PBS equivalent of Slurm's --chdir, so cd explicitly (parity with the
        # local backend's cwd).
        return ["", f"cd {shlex.quote(str(job_dir))} || exit 1"]

    def _submit_command(self, script_path: Path) -> list[str]:
        return ["qsub", str(script_path)]

    def _parse_submit(self, stdout: str) -> str:
        # qsub prints the full job id, e.g. "1234.pbsserver".
        return stdout.strip()

    def _status_command(self, job_id: str) -> list[str]:
        return ["qstat", "-x", "-f", job_id]

    def _parse_status(self, stdout: str) -> PollResult | _Gone:
        fields = _parse_qstat(stdout)
        state = fields.get("job_state", "").upper()
        if state in _RUNNING_STATES:
            return PollResult(status=JobStatus.RUNNING)
        # PBS Pro spells it Exit_status, Torque exit_status.
        exit_status = fields.get("Exit_status", fields.get("exit_status"))
        if exit_status is not None:
            code = _to_int(exit_status)
            status = JobStatus.DONE if code == 0 else JobStatus.FAILED
            return PollResult(status=status, exit_code=code)
        if state in _FINISHED_STATES:
            return GONE  # finished with no outcome recorded: the marker tells
        return PollResult(status=JobStatus.QUEUED)

    def _unknown_job(self, result: "subprocess.CompletedProcess[str]") -> bool:
        # Without job history a finished job is forgotten at once.
        return "Unknown Job Id" in result.stderr

    def _cancel_command(self, job_id: str) -> list[str]:
        return ["qdel", job_id]


def _job_name(raw: str) -> str:
    """A PBS-safe job name: leading letter, alphanumeric, capped length."""
    cleaned = "".join(ch for ch in raw if ch.isalnum())
    return f"j{cleaned}"[:15]


def _walltime(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _parse_qstat(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        if "=" in raw:
            key, _, value = raw.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
