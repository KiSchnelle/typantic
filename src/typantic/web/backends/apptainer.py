"""Apptainer backend: run a job inside an Apptainer/Singularity container.

Apptainer has no daemon and runs the container in the foreground as the invoking
user, with the host filesystem bind-mounted by default — so it behaves like the
local backend with the command wrapped in ``apptainer exec <image>``. The job
dir (and thus the ``--config`` path) is visible in the container without extra
mounts.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web.backends.process import ProcessBackend


class ApptainerOptions(BaseModel):
    """Which image to run and any extra bind mounts."""

    model_config = ConfigDict(extra="forbid")

    image: str = Field(description="Container image: a .sif file or a docker:// URI.")
    binds: list[str] = Field(
        default_factory=list,
        description="Extra --bind mounts, each 'src' or 'src:dst'.",
    )


class ApptainerBackend(ProcessBackend):
    """Run a job inside an Apptainer container (foreground, no daemon)."""

    options_model: ClassVar[type[BaseModel]] = ApptainerOptions

    def _wrap(
        self,
        argv: list[str],
        *,
        job_dir: Path,  # noqa: ARG002 - host FS is bind-mounted by default
        backend_options: dict[str, Any],
    ) -> list[str]:
        """Wrap ``argv`` as ``apptainer exec <image> <argv>``."""
        opts = ApptainerOptions.model_validate(backend_options)
        cmd = ["apptainer", "exec"]
        for bind in opts.binds:
            cmd += ["--bind", bind]
        cmd.append(opts.image)
        return [*cmd, *argv]
