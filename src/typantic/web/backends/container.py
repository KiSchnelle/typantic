"""Container backend: run a job inside a Docker/Podman container.

``docker`` and ``podman`` share this one implementation (their CLIs are
compatible), selected by ``executable``. The command runs in the foreground as a
detached local subprocess, so its output streams to the job log and its exit
status is recorded the same way as a local job; the client forwards SIGTERM to
the container on cancel. The job dir is bind-mounted at the same path
(``-v <job_dir>:<job_dir> -w <job_dir>``) so the ``--config`` path resolves
inside the container.

The container runs with ``--init``: a process running as PID 1 ignores SIGTERM
unless it installs a handler, so without an init in front of it a Python app
shrugged off every cancel. It is also named ``typantic-<job id>``, so a cancel
can stop it by name when the client process is gone -- the daemon owns the
container, which then runs on.
"""

import contextlib
import subprocess
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web._subprocess import run_tool
from typantic.web.backends.process import ProcessBackend
from typantic.web.models import JobRecord

_KILL_TIMEOUT_S = 30


def _container_name(job_dir: Path) -> str:
    """The name a job's container runs under: ``typantic-<job id>``."""
    return f"typantic-{job_dir.name}"


class ContainerOptions(BaseModel):
    """Which image to run and any extra volume mounts."""

    model_config = ConfigDict(extra="forbid")

    image: str = Field(description="Container image to run.")
    volumes: list[str] = Field(
        default_factory=list,
        description="Extra -v mounts, each 'src:dst'.",
    )


class ContainerBackend(ProcessBackend):
    """Run a job inside a Docker/Podman container."""

    options_model: ClassVar[type[BaseModel]] = ContainerOptions

    def __init__(self, executable: str) -> None:
        """Create the backend for ``executable`` ('docker' or 'podman')."""
        self.executable = executable

    def _wrap(
        self,
        argv: list[str],
        *,
        job_dir: Path,
        backend_options: dict[str, Any],
    ) -> list[str]:
        """Wrap ``argv`` as ``<exe> run --rm --init --name … -v … <image> <argv>``."""
        opts = ContainerOptions.model_validate(backend_options)
        run = [
            self.executable,
            "run",
            "--rm",
            "--init",
            "--name",
            _container_name(job_dir),
            "-v",
            f"{job_dir}:{job_dir}",
            "-w",
            str(job_dir),
        ]
        for volume in opts.volumes:
            run += ["-v", volume]
        run.append(opts.image)
        return [*run, *argv]

    def cancel(self, record: JobRecord) -> None:
        """Signal the client, else kill the container by name (best effort)."""
        if self._signal(record):
            return  # the client forwards the signal into the container
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run_tool(
                [self.executable, "kill", _container_name(Path(record.job_dir))],
                timeout=_KILL_TIMEOUT_S,
            )


def docker_backend() -> ContainerBackend:
    """A container backend using the ``docker`` CLI."""
    return ContainerBackend("docker")


def podman_backend() -> ContainerBackend:
    """A container backend using the ``podman`` CLI."""
    return ContainerBackend("podman")
