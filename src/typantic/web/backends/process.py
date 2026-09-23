"""Process backends: run a job as a detached local subprocess.

The job is spawned in a new session (``start_new_session=True``) so it outlives
the web process — a restart never kills a running job, and a still-running job
is re-attached by polling its pid. A detached child is reparented away from us,
so we cannot ``waitpid`` for its exit code; instead the launch wraps the command
so it records its exit code into a ``.typantic-exit`` marker in the job dir when
it finishes. That on-disk marker is the durable truth ``poll`` reads back.

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
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from typantic.web.backends._marker import (
    EXIT_MARKER,
    clear_exit_code,
    read_exit_code,
)
from typantic.web.backends.base import ForeignHostError, Launched, PollResult
from typantic.web.models import JobRecord, JobStatus

_PROC = Path("/proc")


def _pid_start_time(pid: int) -> int | None:
    """The pid's start-time (jiffies since boot) from ``/proc``, or ``None``.

    A ``(pid, start-time)`` pair identifies a specific process *instance*, so it
    survives the pid being recycled onto a different process after a restart.
    ``/proc`` is Linux-only; where it is absent the read fails and we return
    ``None``, and the caller falls back to a bare liveness probe.
    """
    try:
        stat = (_PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The comm field (2) can contain spaces and ')', so split after the final
    # ')': the remaining fields start at field 3 (state) and starttime is 22.
    try:
        return int(stat[stat.rindex(")") + 1 :].split()[19])
    except (ValueError, IndexError):
        return None


def _reap(pid: int, *, attempts: int = 20, delay: float = 0.005) -> None:
    """Harvest a finished child so it doesn't linger as a zombie.

    ``poll`` calls this the moment it first reads the exit-code marker, which the
    wrapper writes *just before* it exits -- so the ``sh`` may not be reapable
    yet. A single ``WNOHANG`` then harvests nothing and, because the record is
    now terminal and never polled again, the child would linger defunct for the
    server's lifetime. Retry briefly until ``waitpid`` collects it (or it turns
    out not to be our child, e.g. reparented across a restart).
    """
    with contextlib.suppress(OSError):
        for _ in range(attempts):
            try:
                reaped, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return  # not our child -- nothing to harvest
            if reaped == pid:
                return  # harvested
            time.sleep(delay)


def _process_running(pid: int, pid_start: int | None = None) -> bool:
    """Whether ``pid`` is still executing our job (not reaped, not recycled).

    A detached job spawned by this (long-lived) process stays its child, so on
    exit it becomes a zombie that ``os.kill(pid, 0)`` still reports as alive. We
    first reap it non-blocking with ``waitpid``; if that harvests it, it has
    exited. After a restart the job is no longer our child (reparented to init),
    ``waitpid`` raises ``ChildProcessError``, and we fall back to a signal-0
    liveness probe.

    ``pid_start`` is the start-time recorded at launch. After a restart a bare
    signal-0 probe cannot tell our job from a *recycled* pid now naming an
    unrelated process; when we can read the pid's current start-time and it
    differs, the pid has been recycled and our job is gone. So has it when the
    probe is refused: the tracked pid is always our own shell, so a pid we may
    not signal belongs to another user by now.
    """
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        reaped_pid = 0  # not our child (e.g. after a restart)
    if reaped_pid == pid:
        return False  # just exited; zombie harvested
    if pid_start is not None:
        current = _pid_start_time(pid)
        if current is not None and current != pid_start:
            return False  # pid recycled onto a different process
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _elsewhere(record: JobRecord) -> bool:
    """Whether the job's process runs on another host than this one."""
    return record.host is not None and record.host != socket.gethostname()


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
        # A restart re-runs in place: a previous run's marker would report the
        # fresh run as already finished.
        clear_exit_code(job_dir)
        marker = shlex.quote(str(job_dir / EXIT_MARKER))
        script = f"{shlex.join(wrapped)}\necho $? > {marker}\n"
        with log_path.open("wb") as log:
            process = subprocess.Popen(  # noqa: S603
                ["/bin/sh", "-c", script],
                stdout=log,
                stderr=subprocess.STDOUT,
                # Without this the job inherits the server's stdin and a command
                # that reads it would consume the operator's terminal input.
                stdin=subprocess.DEVNULL,
                cwd=job_dir,
                start_new_session=True,
            )
        return Launched(
            pid=process.pid,
            pid_start=_pid_start_time(process.pid),
            host=socket.gethostname(),
            status=JobStatus.RUNNING,
        )

    def poll(self, record: JobRecord) -> PollResult:
        """Resolve status from the exit-code marker, else the pid's liveness."""
        job_dir = Path(record.job_dir)
        exit_code = read_exit_code(job_dir)
        if exit_code is None and _elsewhere(record):
            # This host's process table knows nothing of the pid; the exit
            # marker, on the shared filesystem, will tell how the job ended.
            return PollResult(status=record.status, exit_code=record.exit_code)
        if exit_code is None:
            if record.pid is not None and _process_running(
                record.pid,
                record.pid_start,
            ):
                return PollResult(status=JobStatus.RUNNING)
            # The wrapper writes the marker just before it exits, so a job that
            # finished between the two checks has one now.
            exit_code = read_exit_code(job_dir)
            if exit_code is None:
                # Gone without recording an exit code: crashed or was killed.
                return PollResult(status=JobStatus.FAILED)
        if record.pid is not None:
            _reap(record.pid)  # the wrapper is done; don't leave a zombie
        status = JobStatus.DONE if exit_code == 0 else JobStatus.FAILED
        return PollResult(status=status, exit_code=exit_code)

    def cancel(self, record: JobRecord) -> None:
        """SIGTERM the job's process group, if that process is still our job."""
        self._signal(record)

    def _signal(self, record: JobRecord) -> bool:
        """SIGTERM the job's process group; whether it was there to signal.

        A stale row's pid can name an unrelated process of the same user by now
        (the job exited and the pid was reused, say across a server restart), and
        signalling its group would kill that. So the pid is signalled only while
        it still runs with the start time recorded at launch (where ``/proc``
        tells), and still leads its own process group -- as the job's shell does,
        having been started in a new session.

        Raises:
            ForeignHostError: If the job runs on another host: its pid names
                nothing, or something else, here.
        """
        if _elsewhere(record):
            msg = (
                f"Job {record.id} runs on {record.host}; cancel it from a "
                f"dashboard on that host."
            )
            raise ForeignHostError(msg)
        pid = record.pid
        if pid is None or not _process_running(pid, record.pid_start):
            return False
        try:
            if os.getpgid(pid) != pid:
                return False
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
        return True

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
