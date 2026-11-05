"""The exit marker a job leaves in its folder once its command has run.

Both backend families wrap the command so that when it returns, its exit code is
written to ``.typantic-exit`` in the job folder. The file outlives the web
server, the job's process and the scheduler's memory of the job, so it is the
first thing a poll reads.
"""

from datetime import UTC, datetime
from pathlib import Path

from typantic.web.backends.base import PollResult
from typantic.web.models import JobStatus

EXIT_MARKER = ".typantic-exit"


def _read_exit_code(path: Path) -> int | None:
    """Return the recorded exit code, or ``None`` if not yet cleanly written."""
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def finished(job_dir: Path) -> PollResult | None:
    """How the job in ``job_dir`` ended, from its exit marker; ``None`` if not yet.

    The marker's mtime is when the job finished, which a server that was not
    running at the time would otherwise only know as the moment it noticed.
    """
    path = job_dir / EXIT_MARKER
    code = _read_exit_code(path)
    if code is None:
        return None
    status = JobStatus.DONE if code == 0 else JobStatus.FAILED
    return PollResult(status=status, exit_code=code, finished_at=_mtime(path))


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def clear_exit_code(job_dir: Path) -> None:
    """Remove a previous run's marker, so a restart in place starts unfinished."""
    (job_dir / EXIT_MARKER).unlink(missing_ok=True)
