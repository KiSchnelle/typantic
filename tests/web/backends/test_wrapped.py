from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from typantic.web.backends.apptainer import ApptainerBackend
from typantic.web.backends.container import (
    ContainerBackend,
    docker_backend,
    podman_backend,
)
from typantic.web.backends.ssh import SshBackend, SshOptions

ARGV = ["app", "run", "--config", "/jobs/j/submit_config.json"]
JOB_DIR = Path("/jobs/j")


def _wrap(backend, options):
    return backend._wrap(ARGV, job_dir=JOB_DIR, backend_options=options)


# --- SSH ---


def test_ssh_wrap_minimal():
    assert _wrap(SshBackend(), {"host": "node1"}) == [
        "ssh",
        "node1",
        "app run --config /jobs/j/submit_config.json",
    ]


def test_ssh_wrap_full():
    wrapped = _wrap(
        SshBackend(),
        {
            "host": "node1",
            "user": "alice",
            "port": 2222,
            "identity": "/keys/id",
            "directory": "/work dir",
        },
    )
    assert wrapped == [
        "ssh",
        "-p",
        "2222",
        "-i",
        "/keys/id",
        "alice@node1",
        "cd '/work dir' && app run --config /jobs/j/submit_config.json",
    ]


def test_ssh_target_property():
    assert SshOptions(host="h").target == "h"
    assert SshOptions(host="h", user="u").target == "u@h"


def test_ssh_requires_host():
    with pytest.raises(ValidationError):
        _wrap(SshBackend(), {"user": "alice"})


def test_ssh_forbids_extra_options():
    with pytest.raises(ValidationError):
        _wrap(SshBackend(), {"host": "h", "bogus": 1})


# --- Apptainer ---


def test_apptainer_wrap_minimal():
    assert _wrap(ApptainerBackend(), {"image": "tool.sif"}) == [
        "apptainer",
        "exec",
        "tool.sif",
        *ARGV,
    ]


def test_apptainer_wrap_with_binds():
    wrapped = _wrap(
        ApptainerBackend(),
        {"image": "docker://tool:1", "binds": ["/data", "/ref:/ref"]},
    )
    assert wrapped == [
        "apptainer",
        "exec",
        "--bind",
        "/data",
        "--bind",
        "/ref:/ref",
        "docker://tool:1",
        *ARGV,
    ]


def test_apptainer_requires_image():
    with pytest.raises(ValidationError):
        _wrap(ApptainerBackend(), {})


# --- Container (docker / podman) ---


def test_container_wrap_docker():
    wrapped = _wrap(docker_backend(), {"image": "tool:1"})
    assert wrapped == [
        "docker",
        "run",
        "--rm",
        "-v",
        "/jobs/j:/jobs/j",
        "-w",
        "/jobs/j",
        "tool:1",
        *ARGV,
    ]


def test_container_wrap_podman_with_volumes():
    wrapped = _wrap(podman_backend(), {"image": "tool:1", "volumes": ["/data:/data"]})
    assert wrapped[0] == "podman"
    assert "-v" in wrapped
    assert "/data:/data" in wrapped
    assert wrapped[-len(ARGV) - 1] == "tool:1"


def test_container_executable_selectable():
    assert docker_backend().executable == "docker"
    assert podman_backend().executable == "podman"
    assert ContainerBackend("nerdctl").executable == "nerdctl"


def test_container_requires_image():
    with pytest.raises(ValidationError):
        _wrap(docker_backend(), {})


# --- options schemas exposed for the UI ---


@pytest.mark.parametrize(
    "backend",
    [SshBackend(), ApptainerBackend(), docker_backend()],
)
def test_backends_expose_options_model(backend):
    assert issubclass(backend.options_model, BaseModel)
