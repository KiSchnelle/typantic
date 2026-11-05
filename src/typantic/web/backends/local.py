"""Local backend: run a job as a detached subprocess on the web host."""

from typing import ClassVar

from typantic.web.backends.process import ProcessBackend


class LocalBackend(ProcessBackend):
    """Run a job as a detached local subprocess (the argv unchanged)."""

    output_paths_are_local: ClassVar[bool] = True
