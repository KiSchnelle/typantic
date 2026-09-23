"""The exit marker a job leaves in its folder once its command has run.

Both backend families wrap the command so that when it returns, its exit code is
written to ``.typantic-exit`` in the job folder. The file outlives the web
server, the job's process and the scheduler's memory of the job, so it is the
first thing a poll reads.
"""

from pathlib import Path

EXIT_MARKER = ".typantic-exit"
# The marker's name before 0.8.0, which an app could collide with; a job launched
# by an older typantic and still running across the upgrade writes it.
_LEGACY_EXIT_MARKER = "exit_code"


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


def read_exit_code(job_dir: Path) -> int | None:
    """The exit code a finished job recorded in ``job_dir``, else ``None``."""
    for name in (EXIT_MARKER, _LEGACY_EXIT_MARKER):
        code = _read_exit_code(job_dir / name)
        if code is not None:
            return code
    return None


def clear_exit_code(job_dir: Path) -> None:
    """Remove a previous run's markers, so a restart in place starts unfinished."""
    for name in (EXIT_MARKER, _LEGACY_EXIT_MARKER):
        (job_dir / name).unlink(missing_ok=True)
