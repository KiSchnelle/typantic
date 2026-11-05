"""Apptainer backend: run a job inside an Apptainer/Singularity container.

Apptainer has no daemon and runs the container in the foreground as the invoking
user, so it behaves like the local backend with the command wrapped in
``apptainer exec <image>``. Apptainer binds only its default paths into the
container: ``$HOME``, ``/tmp``, ``/var/tmp``, ``/proc``, ``/sys``, ``/dev`` and
the working directory. The job runs in its own folder, so that folder (and thus
the ``--config`` path) is visible without extra mounts. An input or output folder
anywhere else has to be listed in ``binds``.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web.backends.process import ProcessBackend


class ApptainerOptions(BaseModel):
    """Which image to run and any extra bind mounts."""

    model_config = ConfigDict(extra="forbid")

    image: str = Field(
        description="Container image: a .sif file or a docker:// URI.",
        pattern=r"^[^-]",  # "--writable" would be an exec option, not an image
    )
    binds: list[str] = Field(
        default_factory=list,
        description=(
            "Extra --bind mounts, each 'src' or 'src:dst'. Apptainer's default binds "
            "cover your home, /tmp and the job's folder, not the whole host, so list "
            "any input or output folder elsewhere here."
        ),
    )


class ApptainerBackend(ProcessBackend):
    """Run a job inside an Apptainer container (foreground, no daemon)."""

    options_model: ClassVar[type[BaseModel]] = ApptainerOptions

    def _wrap(
        self,
        argv: list[str],
        *,
        job_dir: Path,  # noqa: ARG002 - the working directory is bound by default
        backend_options: dict[str, Any],
    ) -> list[str]:
        """Wrap ``argv`` as ``apptainer exec <image> <argv>``."""
        opts = ApptainerOptions.model_validate(backend_options)
        cmd = ["apptainer", "exec"]
        for bind in opts.binds:
            cmd += ["--bind", bind]
        cmd.append(opts.image)
        return [*cmd, *argv]
