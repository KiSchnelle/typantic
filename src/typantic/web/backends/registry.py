"""Discover launch backends from the ``typantic.web_backends`` entry-point group.

Each entry resolves to a factory (a class or function) returning a
:class:`LaunchBackend`. The built-ins (local, ssh, slurm, pbs, docker, podman,
apptainer) are registered in typantic's own ``pyproject.toml``; a third-party
backend is a pure registry addition — no core edit.
"""

import logging
from importlib.metadata import entry_points

from typantic.web.backends.base import LaunchBackend

_ENTRY_POINT_GROUP = "typantic.web_backends"

logger = logging.getLogger("typantic.web")


def load_backends() -> dict[str, LaunchBackend]:
    """Return every installed backend keyed by its registry name.

    A backend whose factory fails to import or construct is skipped with a
    warning, so one broken backend never hides the rest.
    """
    backends: dict[str, LaunchBackend] = {}
    for entry in entry_points(group=_ENTRY_POINT_GROUP):
        try:
            factory = entry.load()
            backend = factory()
        except Exception:  # a broken backend must not hide the others
            logger.exception("Failed to load backend %r", entry.name)
            continue
        backends[entry.name] = backend
    return backends
