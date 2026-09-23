"""Running a short-lived tool: an app's ``--schema``, a scheduler's query.

Three things the plain ``subprocess.run(..., text=True)`` gets wrong for a web
server that shells out on every request:

- The child inherits the server's stdin, so a tool that prompts reads the
  operator's terminal and hangs until the timeout.
- Output is decoded strictly, so one byte that is not UTF-8 -- a native
  library's warning on stderr, a Latin-1 job name from ``sacct`` -- raises
  ``UnicodeDecodeError`` out of the call and turns the request into an HTTP 500.
- A timeout kills only the direct child; a wrapper script's children keep
  running.

:func:`run_tool` runs the tool with no stdin, captures bytes and decodes them
with replacement, and runs it in its own session so a timeout kills the whole
process group.
"""

import contextlib
import os
import signal
import subprocess


def run_tool(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` to completion and return its exit code and decoded output.

    Args:
        argv: The command, executable first (no shell).
        timeout: Seconds to wait before the whole process group is killed.

    Returns:
        The completed process; stdout/stderr are UTF-8 decoded with replacement.

    Raises:
        OSError: If the executable could not be run at all.
        subprocess.TimeoutExpired: If it outlived ``timeout`` (and was killed).
    """
    with subprocess.Popen(  # noqa: S603 - argv is the caller's fixed command, no shell
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            raise
    return subprocess.CompletedProcess(
        argv,
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
