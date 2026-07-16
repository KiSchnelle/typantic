"""Launch backends: turn a built argv into a tracked job.

Every backend shares one contract (:class:`LaunchBackend`): ``launch`` starts
the job and returns a handle, ``poll`` re-resolves its status, and ``cancel``
stops it. Backends are discovered by key from the ``typantic.web_backends``
entry-point group (see :func:`load_backends`).

Two families ship built in: the *process* family (``local``, ``ssh``,
``apptainer``, ``docker``, ``podman``) run a possibly-wrapped command as a
detached local subprocess; the *scheduler* family (``slurm``, ``pbs``) submit a
batch script and track it by job id.
"""

from typantic.web.backends.apptainer import ApptainerBackend
from typantic.web.backends.base import LaunchBackend, Launched, PollResult
from typantic.web.backends.container import ContainerBackend
from typantic.web.backends.local import LocalBackend
from typantic.web.backends.pbs import PbsBackend
from typantic.web.backends.process import ProcessBackend
from typantic.web.backends.registry import load_backends
from typantic.web.backends.scheduler import (
    SchedulerBackend,
    SchedulerError,
    SchedulerParams,
)
from typantic.web.backends.slurm import SlurmBackend
from typantic.web.backends.ssh import SshBackend

__all__ = [
    "ApptainerBackend",
    "ContainerBackend",
    "LaunchBackend",
    "Launched",
    "LocalBackend",
    "PbsBackend",
    "PollResult",
    "ProcessBackend",
    "SchedulerBackend",
    "SchedulerError",
    "SchedulerParams",
    "SlurmBackend",
    "SshBackend",
    "load_backends",
]
