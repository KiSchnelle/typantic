"""Process backends: run a job as a detached local subprocess.

The job is spawned in a new session (``start_new_session=True``) so it outlives
the web process — a restart never kills a running job, and a still-running job
is re-attached by polling its pid. A detached child is reparented away from us,
so we cannot ``waitpid`` for its exit code; instead the launch wraps the command
so it records its exit code into an ``exit_code`` marker in the job dir when it
finishes. That on-disk marker is the durable truth ``poll`` reads back.

Subclasses override :meth:`ProcessBackend._wrap` to run the command through
something else (SSH to a remote host, a container runtime, …) while reusing all
of the spawn / poll / cancel machinery. The wrapper's own exit status is what
gets recorded, and ``ssh`` / ``docker`` / ``apptainer`` all propagate the inner
command's exit code and forward signals, so tracking stays identical.
"""

import contextlib
import os
import shlex
import signal
import subprocess
from pathlib import Path
from typing import Any

from typantic.web.backends.base import Launched, PollResult
from typantic.web.models import JobRecord, JobStatus

_EXIT_CODE_FILE = "exit_code"


def _exit_code_path(job_dir: Path) -> Path:
    return job_dir / _EXIT_CODE_FILE


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


def _reap(pid: int) -> None:
    """Harvest a finished child so it doesn't linger as a zombie (non-blocking)."""
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def _process_running(pid: int) -> bool:
    """Whether ``pid`` is still executing (not a reaped/zombie child).

    A detached job spawned by this (long-lived) process stays its child, so on
    exit it becomes a zombie that ``os.kill(pid, 0)`` still reports as alive. We
    first reap it non-blocking with ``waitpid``; if that harvests it, it has
    exited. After a restart the job is no longer our child (reparented to init),
    ``waitpid`` raises ``ChildProcessError``, and we fall back to a signal-0
    liveness probe.
    """
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        reaped_pid = 0  # not our child (e.g. after a restart)
    if reaped_pid == pid:
        return False  # just exited; zombie harvested
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


class ProcessBackend:
    """Spawn and track jobs as detached local subprocesses."""

    def _wrap(
        self,
        argv: list[str],
        *,
        job_dir: Path,  # noqa: ARG002 - used by subclasses
        backend_options: dict[str, Any],  # noqa: ARG002 - used by subclasses
    ) -> list[str]:
        """Transform the argv before it is spawned. Base runs it unchanged."""
        return argv

    def launch(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,
        backend_options: dict[str, Any],
    ) -> Launched:
        """Spawn the (wrapped) command detached, capturing output to ``log_path``."""
        wrapped = self._wrap(argv, job_dir=job_dir, backend_options=backend_options)
        exit_path = _exit_code_path(job_dir)
        # Clear any marker from a previous run in this dir (restart re-runs in
        # place); otherwise poll() would read the stale code and report the fresh
        # run as already finished.
        exit_path.unlink(missing_ok=True)
        script = f"{shlex.join(wrapped)}\necho $? > {shlex.quote(str(exit_path))}\n"
        with log_path.open("wb") as log:
            process = subprocess.Popen(  # noqa: S603
                ["/bin/sh", "-c", script],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=job_dir,
                start_new_session=True,
            )
        return Launched(pid=process.pid, status=JobStatus.RUNNING)

    def poll(self, record: JobRecord) -> PollResult:
        """Resolve status from the exit-code marker, else the pid's liveness."""
        exit_code = _read_exit_code(_exit_code_path(Path(record.job_dir)))
        if exit_code is not None:
            if record.pid is not None:
                _reap(record.pid)  # the wrapper is done; don't leave a zombie
            status = JobStatus.DONE if exit_code == 0 else JobStatus.FAILED
            return PollResult(status=status, exit_code=exit_code)
        if record.pid is not None and _process_running(record.pid):
            return PollResult(status=JobStatus.RUNNING)
        # Gone without recording an exit code: crashed or was killed.
        return PollResult(status=JobStatus.FAILED)

    def cancel(self, record: JobRecord) -> None:
        """SIGTERM the job's process group (best effort)."""
        if record.pid is None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGTERM)

    def preview(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        log_path: Path,  # noqa: ARG002 - the log is chosen at launch, not shown here
        backend_options: dict[str, Any],
    ) -> str:
        """Return the wrapped shell command this launch would run."""
        return shlex.join(
            self._wrap(argv, job_dir=job_dir, backend_options=backend_options),
        )
