from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from typantic.web.backends import container as container_mod
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
        # PID 1 ignores SIGTERM unless it handles it: without an init, a Python
        # app in a container shrugged off every cancel.
        "--init",
        # A name to stop it by, should the client process be gone.
        "--name",
        "typantic-j",
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


# --- a cancelled container stops ---


def _record(job_dir):
    from datetime import UTC, datetime  # noqa: PLC0415

    from typantic.web.models import JobRecord  # noqa: PLC0415

    return JobRecord(
        id=job_dir.name,
        command_key="a/b",
        app="a",
        command="b",
        title="T",
        backend="docker",
        job_dir=str(job_dir),
        config_path=str(job_dir / "submit_config.json"),
        log_path=str(job_dir / "job.log"),
        pid=4242,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def kills(monkeypatch):
    calls = []
    monkeypatch.setattr(container_mod, "run_tool", lambda argv, **_: calls.append(argv))
    return calls


def _client(monkeypatch, *, reachable):
    backend = container_mod.ContainerBackend
    monkeypatch.setattr(backend, "_signal", lambda _self, _record: reachable)


def test_cancel_signals_the_client_when_it_is_there(monkeypatch, kills):
    _client(monkeypatch, reachable=True)
    docker_backend().cancel(_record(JOB_DIR))
    assert kills == []


def test_cancel_kills_the_container_by_name_when_the_client_is_gone(monkeypatch, kills):
    # The daemon owns the container: with the docker client dead, signalling
    # the job's process group reached nothing and the container ran on.
    _client(monkeypatch, reachable=False)
    podman_backend().cancel(_record(JOB_DIR))
    assert kills == [["podman", "kill", "typantic-j"]]


def test_cancel_survives_a_missing_container_cli(monkeypatch):
    def missing(argv, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    _client(monkeypatch, reachable=False)
    monkeypatch.setattr(container_mod, "run_tool", missing)
    docker_backend().cancel(_record(JOB_DIR))


# --- options that would be read as command-line options ---


@pytest.mark.parametrize(
    ("backend", "options"),
    [
        # user@host: "-oProxyCommand=sh -c ..." is an ssh option, not a user.
        (SshBackend(), {"host": "h", "user": "-oProxyCommand=touch /tmp/x"}),
        # docker run <image>: "--privileged" is a docker option, not an image.
        (docker_backend(), {"image": "--privileged"}),
        (podman_backend(), {"image": "-v/:/host"}),
        (ApptainerBackend(), {"image": "--writable"}),
    ],
)
def test_an_option_cannot_pose_as_a_value(backend, options):
    with pytest.raises(ValidationError):
        _wrap(backend, options)


# --- ssh: a remote directory under the remote home ---


@pytest.mark.parametrize(
    ("directory", "cd"),
    [
        # Quoted whole, "~/work dir" named a folder literally called "~".
        ("~/work dir", "cd ~/'work dir'"),
        ("~", "cd ~"),
        ("/abs/~x", "cd '/abs/~x'"),  # only a leading ~ is the home
    ],
)
def test_ssh_directory_under_the_remote_home(directory, cd):
    wrapped = _wrap(SshBackend(), {"host": "h", "directory": directory})
    assert wrapped[-1] == f"{cd} && app run --config /jobs/j/submit_config.json"
