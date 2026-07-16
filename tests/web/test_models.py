from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from typantic.web.models import (
    CommandMeta,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    MakeDirRequest,
    Project,
)


def _meta(**over):
    base = {"app": "myapp", "command": "run", "argv": ("run",), "title": "Run"}
    return CommandMeta(**{**base, **over})


def test_command_meta_key_and_defaults():
    meta = _meta()
    assert meta.key == "myapp/run"
    assert meta.default_backend == "local"
    assert meta.description == ""


def test_command_meta_invocation():
    meta = _meta(argv=("group", "run"))
    assert meta.invocation() == ["myapp", "group", "run"]
    assert meta.invocation("--schema") == ["myapp", "group", "run", "--schema"]
    assert meta.invocation("--config", "job.yaml") == [
        "myapp",
        "group",
        "run",
        "--config",
        "job.yaml",
    ]


def test_command_meta_ignores_unknown_keys():
    meta = _meta(future_field="ignored")
    assert not hasattr(meta, "future_field")


def test_command_meta_frozen():
    meta = _meta()
    with pytest.raises(ValidationError):
        meta.title = "changed"


def test_launch_request_defaults_and_forbid_extra():
    req = LaunchRequest(command_key="myapp/run", backend="local")
    assert req.values == {}
    assert req.backend_options == {}
    assert req.project_id is None
    assert req.name is None
    with pytest.raises(ValidationError):
        LaunchRequest(command_key="a/b", backend="local", bogus=1)


def test_make_dir_request():
    r = MakeDirRequest(path="/data", name="new")
    assert r.path == "/data"
    with pytest.raises(ValidationError):
        MakeDirRequest(path="/data", name="new", extra=1)


def test_launch_preview_optional_script():
    p = LaunchPreview(config="{}", argv=["myapp", "run"])
    assert p.script is None


def test_project():
    proj = Project(id="p1", name="Screen A", created_at=datetime.now(UTC))
    assert proj.description == ""


def _record(status: JobStatus) -> JobRecord:
    return JobRecord(
        id="j1",
        command_key="myapp/run",
        app="myapp",
        command="run",
        title="Run",
        backend="local",
        job_dir="/jobs/j1",
        config_path="/jobs/j1/submit_config.json",
        log_path="/jobs/j1/job.log",
        status=status,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        (JobStatus.QUEUED, False),
        (JobStatus.RUNNING, False),
        (JobStatus.DONE, True),
        (JobStatus.FAILED, True),
        (JobStatus.CANCELLED, True),
    ],
)
def test_job_record_is_terminal(status, terminal):
    assert _record(status).is_terminal is terminal
