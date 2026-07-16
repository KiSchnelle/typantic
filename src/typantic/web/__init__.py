"""typantic's web layer — a generic job launcher, dashboard, and form bridge.

Requires the optional ``[web]`` extra (``pip install 'typantic[web]'``). The
base ``import typantic`` never imports this package, so installs that only need
the CLI bridge stay free of FastAPI and friends.
"""

from typantic.web.models import (
    CommandMeta,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    MakeDirRequest,
    Project,
)
from typantic.web.schema import (
    SchemaCache,
    SchemaError,
    fetch_schema,
    normalize_for_form,
)

__all__ = [
    "CommandMeta",
    "JobRecord",
    "JobStatus",
    "LaunchPreview",
    "LaunchRequest",
    "MakeDirRequest",
    "Project",
    "SchemaCache",
    "SchemaError",
    "fetch_schema",
    "normalize_for_form",
]
