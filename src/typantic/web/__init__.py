"""typantic's web layer — a generic job launcher, dashboard, and form bridge.

Requires the optional ``[web]`` extra (``pip install 'typantic[web]'``). The
base ``import typantic`` never imports this package, so installs that only need
the CLI bridge stay free of FastAPI and friends.
"""

try:
    import fastapi as _fastapi  # noqa: F401
except ModuleNotFoundError as _exc:
    _msg = "typantic.web requires the [web] extra: pip install 'typantic[web]'"
    raise ModuleNotFoundError(_msg) from _exc

from typantic.web.api import make_api
from typantic.web.backends import (
    ApptainerBackend,
    ContainerBackend,
    LaunchBackend,
    Launched,
    LocalBackend,
    PbsBackend,
    PollResult,
    SlurmBackend,
    SshBackend,
    load_backends,
)
from typantic.web.discovery import command_catalog, discover_commands
from typantic.web.launcher import (
    JobNotTerminalError,
    Launcher,
    UnknownBackendError,
    UnknownCommandError,
)
from typantic.web.models import (
    CommandMeta,
    History,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    MakeDirRequest,
    Project,
    ProjectGroup,
)
from typantic.web.schema import (
    SchemaCache,
    SchemaError,
    fetch_schema,
    normalize_for_form,
)
from typantic.web.store import JobStore, default_jobs_dir

__all__ = [
    "ApptainerBackend",
    "CommandMeta",
    "ContainerBackend",
    "History",
    "JobNotTerminalError",
    "JobRecord",
    "JobStatus",
    "JobStore",
    "LaunchBackend",
    "LaunchPreview",
    "LaunchRequest",
    "Launched",
    "Launcher",
    "LocalBackend",
    "MakeDirRequest",
    "PbsBackend",
    "PollResult",
    "Project",
    "ProjectGroup",
    "SchemaCache",
    "SchemaError",
    "SlurmBackend",
    "SshBackend",
    "UnknownBackendError",
    "UnknownCommandError",
    "command_catalog",
    "default_jobs_dir",
    "discover_commands",
    "fetch_schema",
    "load_backends",
    "make_api",
    "normalize_for_form",
]
