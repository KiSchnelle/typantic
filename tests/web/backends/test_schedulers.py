import subprocess
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from typantic.web.backends.pbs import (
    PbsBackend,
    _job_name,
    _to_int,
    _walltime,
)
from typantic.web.backends.scheduler import (
    SchedulerError,
    SchedulerParams,
    _default_runner,
    first_nonempty_line,
)
from typantic.web.backends.slurm import SlurmBackend
from typantic.web.models import JobRecord, JobStatus


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def set(self, tool, *, returncode=0, stdout="", stderr=""):
        self.responses[tool] = subprocess.CompletedProcess(
            [tool], returncode, stdout, stderr,
        )

    def __call__(self, argv):
        self.calls.append(argv)
        return self.responses.get(
            argv[0],
            subprocess.CompletedProcess(argv, 0, "", ""),
        )


def _record(job_dir, *, scheduler_id=None, status=JobStatus.QUEUED):
    return JobRecord(
        id="j",
        command_key="a/b",
        app="a",
        command="b",
        title="T",
        backend="slurm",
        job_dir=str(job_dir),
        config_path=str(job_dir / "c.json"),
        log_path=str(job_dir / "job.log"),
        scheduler_id=scheduler_id,
        status=status,
        created_at=datetime.now(UTC),
    )


ARGV = ["app", "run", "--config", "/jobs/j/c.json"]
FULL_OPTS = {
    "partition": "gpu",
    "gpus": 2,
    "cpus": 4,
    "mem": "16G",
    "time_minutes": 90,
    "extra": ["--nodes=1"],
}


# --- Slurm ---


def test_slurm_launch_writes_script_and_returns_id(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="12345\n")
    launched = SlurmBackend(runner).launch(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options=FULL_OPTS,
    )
    assert launched.scheduler_id == "12345"
    assert launched.status is JobStatus.QUEUED
    script = (tmp_path / "submit.sh").read_text()
    for directive in (
        "#SBATCH --job-name=",
        f"#SBATCH --output={tmp_path / 'job.log'}",
        "#SBATCH --partition=gpu",
        "#SBATCH --gres=gpu:2",
        "#SBATCH --cpus-per-task=4",
        "#SBATCH --mem=16G",
        "#SBATCH --time=90",
        "#SBATCH --nodes=1",
    ):
        assert directive in script
    assert runner.calls[0] == ["sbatch", "--parsable", str(tmp_path / "submit.sh")]


def test_slurm_launch_strips_cluster_suffix(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="777;clusterA\n")
    launched = SlurmBackend(runner).launch(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "log", backend_options={},
    )
    assert launched.scheduler_id == "777"


def test_slurm_submit_failure_raises(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", returncode=1, stderr="boom")
    with pytest.raises(SchedulerError, match="Submission failed"):
        SlurmBackend(runner).launch(
            ARGV, job_dir=tmp_path, log_path=tmp_path / "log", backend_options={},
        )


def test_slurm_empty_job_id_raises(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="   \n")
    with pytest.raises(SchedulerError, match="did not return a job id"):
        SlurmBackend(runner).launch(
            ARGV, job_dir=tmp_path, log_path=tmp_path / "log", backend_options={},
        )


def test_slurm_poll_without_id_is_failed(tmp_path):
    assert SlurmBackend(FakeRunner()).poll(_record(tmp_path)).status is JobStatus.FAILED


@pytest.mark.parametrize(
    ("sacct", "status", "exit_code"),
    [
        # sacct prints "0:0" for a job that has not finished, so a non-terminal
        # state must report no exit code at all -- PollResult documents exit_code
        # as set "once finished", and the dashboard renders a 0 as "exit 0".
        ("RUNNING|0:0", JobStatus.RUNNING, None),
        ("PENDING|0:0", JobStatus.QUEUED, None),
        ("COMPLETED|0:0", JobStatus.DONE, 0),
        ("FAILED|1:0", JobStatus.FAILED, 1),
        ("CANCELLED by 1001|0:0", JobStatus.CANCELLED, 0),
        ("WEIRD|x:0", JobStatus.QUEUED, None),
        ("", JobStatus.QUEUED, None),
    ],
)
def test_slurm_poll_states(tmp_path, sacct, status, exit_code):
    runner = FakeRunner()
    runner.set("sacct", stdout=sacct)
    result = SlurmBackend(runner).poll(_record(tmp_path, scheduler_id="1"))
    assert result.status is status
    assert result.exit_code == exit_code


def test_slurm_poll_keeps_last_known_status_when_the_query_fails(tmp_path):
    # A failed query says nothing about the job. Reading its empty stdout as
    # "not in the queue" reported a dead cluster as QUEUED forever.
    runner = FakeRunner()
    runner.set("sacct", stdout="", returncode=127, stderr="sacct: not found")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    result = SlurmBackend(runner).poll(record)
    assert result.status is JobStatus.RUNNING


def test_slurm_poll_survives_a_missing_scheduler_tool(tmp_path):
    # Every listed job is refreshed on read, so an exception escaping poll()
    # would 500 the whole jobs list rather than degrade one row.
    def raising(argv):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    result = SlurmBackend(raising).poll(record)
    assert result.status is JobStatus.RUNNING


def test_slurm_cancel(tmp_path):
    runner = FakeRunner()
    SlurmBackend(runner).cancel(_record(tmp_path, scheduler_id="55"))
    assert ["scancel", "55"] in runner.calls


def test_slurm_cancel_without_id_is_noop(tmp_path):
    runner = FakeRunner()
    SlurmBackend(runner).cancel(_record(tmp_path))
    assert runner.calls == []


def test_slurm_preview_returns_script(tmp_path):
    script = SlurmBackend(FakeRunner()).preview(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "log", backend_options={},
    )
    assert script.startswith("#!/bin/bash")
    assert "#SBATCH" in script


# --- PBS ---


def test_pbs_launch_writes_script(tmp_path):
    runner = FakeRunner()
    runner.set("qsub", stdout="1234.pbs\n")
    launched = PbsBackend(runner).launch(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options=FULL_OPTS,
    )
    assert launched.scheduler_id == "1234.pbs"
    script = (tmp_path / "submit.sh").read_text()
    assert "#PBS -N j" in script
    assert "#PBS -j oe" in script
    assert "#PBS -q gpu" in script
    assert "#PBS -l select=1:ncpus=4:ngpus=2:mem=16G" in script
    assert "#PBS -l walltime=01:30:00" in script
    assert "#PBS --nodes=1" in script
    assert runner.calls[0] == ["qsub", str(tmp_path / "submit.sh")]


@pytest.mark.parametrize(
    ("qstat", "status", "exit_code"),
    [
        ("job_state = R", JobStatus.RUNNING, None),
        ("job_state = E", JobStatus.RUNNING, None),
        ("job_state = F\nExit_status = 0", JobStatus.DONE, 0),
        ("job_state = F\nExit_status = 3", JobStatus.FAILED, 3),
        ("job_state = C", JobStatus.DONE, None),
        ("job_state = Q", JobStatus.QUEUED, None),
        ("", JobStatus.QUEUED, None),
    ],
)
def test_pbs_poll_states(tmp_path, qstat, status, exit_code):
    runner = FakeRunner()
    runner.set("qstat", stdout=qstat)
    result = PbsBackend(runner).poll(_record(tmp_path, scheduler_id="1"))
    assert result.status is status
    assert result.exit_code == exit_code


def test_pbs_poll_without_id_is_failed(tmp_path):
    assert PbsBackend(FakeRunner()).poll(_record(tmp_path)).status is JobStatus.FAILED


def test_pbs_cancel(tmp_path):
    runner = FakeRunner()
    PbsBackend(runner).cancel(_record(tmp_path, scheduler_id="9.x"))
    assert ["qdel", "9.x"] in runner.calls


# --- helpers ---


def test_job_name_is_pbs_safe():
    name = _job_name("20260101-1200-abcdef99")
    assert name.startswith("j")
    assert name.isalnum()
    assert len(name) <= 15


def test_walltime_formatting():
    assert _walltime(5) == "00:05:00"
    assert _walltime(90) == "01:30:00"
    assert _walltime(125) == "02:05:00"


def test_to_int():
    assert _to_int("5") == 5
    assert _to_int("nope") is None


# --- shared params ---


def test_scheduler_params_validation():
    with pytest.raises(ValidationError):
        SchedulerParams(unknown=1)
    with pytest.raises(ValidationError):
        SchedulerParams(gpus=-1)
    with pytest.raises(ValidationError):
        SchedulerParams(cpus=0)


def test_scheduler_backends_expose_options_model():
    assert SlurmBackend().options_model is SchedulerParams
    assert PbsBackend().options_model is SchedulerParams


def test_pbs_launch_minimal_omits_optional_directives(tmp_path):
    runner = FakeRunner()
    runner.set("qsub", stdout="7.pbs\n")
    PbsBackend(runner).launch(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    script = (tmp_path / "submit.sh").read_text()
    assert "#PBS -q" not in script
    assert "select=1" not in script
    assert "walltime" not in script


def test_pbs_parse_qstat_ignores_non_kv_lines(tmp_path):
    runner = FakeRunner()
    runner.set("qstat", stdout="Job Id: 1.pbs\njob_state = R\n")
    result = PbsBackend(runner).poll(_record(tmp_path, scheduler_id="1"))
    assert result.status is JobStatus.RUNNING


def test_default_runner_runs_a_command():
    result = _default_runner(["true"])
    assert result.returncode == 0


def test_first_nonempty_line():
    assert first_nonempty_line("\n  \nfirst\nsecond") == "first"
    assert first_nonempty_line("") is None
    assert first_nonempty_line("  \n\t") is None


def test_slurm_unparsable_exit_code_is_none(tmp_path):
    # sacct can print a non-numeric ExitCode field; it must not crash the poll.
    runner = FakeRunner()
    runner.set("sacct", stdout="COMPLETED|weird")
    result = SlurmBackend(runner).poll(_record(tmp_path, scheduler_id="1"))
    assert result.status is JobStatus.DONE
    assert result.exit_code is None
