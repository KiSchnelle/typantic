"""Local backend: run a job as a detached subprocess on the web host."""

from typantic.web.backends.process import ProcessBackend


class LocalBackend(ProcessBackend):
    """Run a job as a detached local subprocess (the argv unchanged)."""
