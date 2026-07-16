"""Container backend: run a job inside a Docker/Podman container.

``docker`` and ``podman`` share this one implementation (their CLIs are
compatible), selected by ``executable``. The command runs in the foreground as a
detached local subprocess, so its output streams to the job log and its exit
status is recorded the same way as a local job; Docker forwards SIGTERM to the
container on cancel. The job dir is bind-mounted at the same path
(``-v <job_dir>:<job_dir> -w <job_dir>``) so the ``--config`` path resolves
inside the container.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web.backends.process import ProcessBackend


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
        """Wrap ``argv`` as ``<exe> run --rm -v <job_dir>:<job_dir> <image> <argv>``."""
        opts = ContainerOptions.model_validate(backend_options)
        run = [
            self.executable,
            "run",
            "--rm",
            "-v",
            f"{job_dir}:{job_dir}",
            "-w",
            str(job_dir),
        ]
        for volume in opts.volumes:
            run += ["-v", volume]
        run.append(opts.image)
        return [*run, *argv]


def docker_backend() -> ContainerBackend:
    """A container backend using the ``docker`` CLI."""
    return ContainerBackend("docker")


def podman_backend() -> ContainerBackend:
    """A container backend using the ``podman`` CLI."""
    return ContainerBackend("podman")
