"""PBS backend: submit via ``qsub``, track via ``qstat -x`` / ``qdel``.

Targets PBS Pro (``qstat -x`` keeps finished jobs in the listing). The submit
script mirrors the Slurm backend's, translated to ``#PBS`` directives.
"""

from pathlib import Path

from typantic.web.backends.base import PollResult
from typantic.web.backends.scheduler import SchedulerBackend, SchedulerParams
from typantic.web.models import JobStatus

# PBS job_state letters we treat as running (E = exiting/epilogue).
_RUNNING_STATES = frozenset({"R", "E"})
# Terminal letters (C = Torque complete, F = PBS Pro finished).
_FINISHED_STATES = frozenset({"C", "F"})


class PbsBackend(SchedulerBackend):
    """Submit and track jobs on a PBS cluster."""

    def _directives(
        self,
        params: SchedulerParams,
        *,
        job_dir: Path,
        log_path: Path,
    ) -> list[str]:
        lines = [
            f"#PBS -N {_job_name(job_dir.name)}",
            f"#PBS -o {log_path}",
            "#PBS -j oe",  # merge stderr into the -o log
        ]
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

    def _submit_command(self, script_path: Path) -> list[str]:
        return ["qsub", str(script_path)]

    def _parse_submit(self, stdout: str) -> str:
        # qsub prints the full job id, e.g. "1234.pbsserver".
        return stdout.strip()

    def _status_command(self, job_id: str) -> list[str]:
        return ["qstat", "-x", "-f", job_id]

    def _parse_status(self, stdout: str) -> PollResult:
        fields = _parse_qstat(stdout)
        state = fields.get("job_state", "").upper()
        if state in _RUNNING_STATES:
            return PollResult(status=JobStatus.RUNNING)
        exit_status = fields.get("Exit_status")
        if exit_status is not None:
            code = _to_int(exit_status)
            status = JobStatus.DONE if code == 0 else JobStatus.FAILED
            return PollResult(status=status, exit_code=code)
        if state in _FINISHED_STATES:
            return PollResult(status=JobStatus.DONE)
        return PollResult(status=JobStatus.QUEUED)

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
