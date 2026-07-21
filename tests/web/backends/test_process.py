import subprocess
import time
from datetime import UTC, datetime

from typantic.web.backends import process as proc
from typantic.web.backends.local import LocalBackend
from typantic.web.backends.process import (
    _process_running,
    _read_exit_code,
    _reap,
)
from typantic.web.models import JobRecord, JobStatus


def _record(job_dir, *, pid=None):
    return JobRecord(
        id="j",
        command_key="a/b",
        app="a",
        command="b",
        title="T",
        backend="local",
        job_dir=str(job_dir),
        config_path=str(job_dir / "submit_config.json"),
        log_path=str(job_dir / "job.log"),
        pid=pid,
        created_at=datetime.now(UTC),
    )


def _wait_for_marker(job_dir, timeout=5.0):
    marker = job_dir / "exit_code"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists() and marker.read_text().strip():
            return
        time.sleep(0.02)
    msg = "exit_code marker was never written"
    raise AssertionError(msg)


# --- LocalBackend end-to-end ---


def test_local_launch_runs_to_completion(tmp_path):
    backend = LocalBackend()
    log = tmp_path / "job.log"
    launched = backend.launch(
        ["sh", "-c", "printf hello"],
        job_dir=tmp_path,
        log_path=log,
        backend_options={},
    )
    assert launched.status is JobStatus.RUNNING
    assert launched.pid is not None
    _wait_for_marker(tmp_path)
    result = backend.poll(_record(tmp_path, pid=launched.pid))
    assert result.status is JobStatus.DONE
    assert result.exit_code == 0
    assert log.read_text() == "hello"


def test_local_launch_failure_maps_to_failed(tmp_path):
    backend = LocalBackend()
    launched = backend.launch(
        ["sh", "-c", "exit 3"],
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    _wait_for_marker(tmp_path)
    result = backend.poll(_record(tmp_path, pid=launched.pid))
    assert result.status is JobStatus.FAILED
    assert result.exit_code == 3


def test_local_running_then_done(tmp_path):
    backend = LocalBackend()
    launched = backend.launch(
        ["sleep", "0.4"],
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    record = _record(tmp_path, pid=launched.pid)
    assert backend.poll(record).status is JobStatus.RUNNING
    _wait_for_marker(tmp_path)
    assert backend.poll(record).status is JobStatus.DONE


def test_local_clears_stale_marker_on_relaunch(tmp_path):
    (tmp_path / "exit_code").write_text("0\n")  # a previous run's marker
    backend = LocalBackend()
    launched = backend.launch(
        ["sleep", "0.3"],
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    # The stale marker was cleared, so the fresh run reads as running, not done.
    assert backend.poll(_record(tmp_path, pid=launched.pid)).status is JobStatus.RUNNING


def test_poll_dead_pid_without_marker_is_failed(tmp_path):
    backend = LocalBackend()
    result = backend.poll(_record(tmp_path, pid=999_999))
    assert result.status is JobStatus.FAILED


def test_poll_no_pid_no_marker_is_failed(tmp_path):
    backend = LocalBackend()
    assert backend.poll(_record(tmp_path, pid=None)).status is JobStatus.FAILED


def test_poll_marker_present_without_pid(tmp_path):
    (tmp_path / "exit_code").write_text("0\n")
    result = LocalBackend().poll(_record(tmp_path, pid=None))
    assert result.status is JobStatus.DONE
    assert result.exit_code == 0


def test_cancel_running_job(tmp_path):
    backend = LocalBackend()
    launched = backend.launch(
        ["sleep", "30"],
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    record = _record(tmp_path, pid=launched.pid)
    backend.cancel(record)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if backend.poll(record).status is JobStatus.FAILED:
            break
        time.sleep(0.02)
    assert backend.poll(record).status is JobStatus.FAILED


def test_local_preview_is_the_plain_command(tmp_path):
    preview = LocalBackend().preview(
        ["app", "run", "--config", "/x"],
        job_dir=tmp_path,
        log_path=tmp_path / "job.log",
        backend_options={},
    )
    assert preview == "app run --config /x"


def test_cancel_no_pid_is_noop(tmp_path):
    LocalBackend().cancel(_record(tmp_path, pid=None))


def test_cancel_already_gone_is_suppressed(tmp_path):
    LocalBackend().cancel(_record(tmp_path, pid=999_999))


# --- helper functions ---


def test_read_exit_code(tmp_path):
    missing = tmp_path / "nope"
    assert _read_exit_code(missing) is None
    bad = tmp_path / "bad"
    bad.write_text("not-a-number")
    assert _read_exit_code(bad) is None
    good = tmp_path / "good"
    good.write_text("7\n")
    assert _read_exit_code(good) == 7


def test_reap_nonexistent_pid_suppressed():
    _reap(999_999)  # no error


def test_reap_retries_until_harvested(monkeypatch):
    # First WNOHANG races the wrapper's exit (nothing reapable yet); the retry
    # then collects it, so the child never lingers defunct.
    results = iter([(0, 0), (4242, 0)])
    monkeypatch.setattr(proc.os, "waitpid", lambda *_a, **_k: next(results))
    monkeypatch.setattr(proc.time, "sleep", lambda _d: None)
    _reap(4242)


def test_reap_gives_up_after_attempts(monkeypatch):
    count = 0

    def never_reapable(*_a, **_k):
        nonlocal count
        count += 1
        return (0, 0)

    monkeypatch.setattr(proc.os, "waitpid", never_reapable)
    monkeypatch.setattr(proc.time, "sleep", lambda _d: None)
    _reap(4242, attempts=3)
    assert count == 3


def test_process_running_true_for_live_child():
    child = subprocess.Popen(["sleep", "2"])  # noqa: S607
    try:
        assert _process_running(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_process_running_false_for_exited_child():
    child = subprocess.Popen(["true"])  # noqa: S607
    time.sleep(0.2)  # let it exit; do NOT wait() so it stays an unreaped child
    assert _process_running(child.pid) is False


def test_process_running_not_our_child_gone(monkeypatch):
    def raise_child(*_a, **_k):
        raise ChildProcessError

    def raise_gone(*_a, **_k):
        raise ProcessLookupError

    monkeypatch.setattr(proc.os, "waitpid", raise_child)
    monkeypatch.setattr(proc.os, "kill", raise_gone)
    assert _process_running(4242) is False


def test_process_running_not_our_child_permission(monkeypatch):
    def raise_child(*_a, **_k):
        raise ChildProcessError

    def raise_perm(*_a, **_k):
        raise PermissionError

    monkeypatch.setattr(proc.os, "waitpid", raise_child)
    monkeypatch.setattr(proc.os, "kill", raise_perm)
    assert _process_running(4242) is True


def test_process_running_not_our_child_alive(monkeypatch):
    def raise_child(*_a, **_k):
        raise ChildProcessError

    monkeypatch.setattr(proc.os, "waitpid", raise_child)
    monkeypatch.setattr(proc.os, "kill", lambda *_a, **_k: None)
    assert _process_running(4242) is True


# --- pid start-time / pid-reuse detection ---


def test_pid_start_time_reads_starttime(monkeypatch, tmp_path):
    (tmp_path / "123").mkdir()
    # comm (field 2) has spaces and a ')'; starttime (field 22) is 987654.
    stat = "123 (sh proc) S " + " ".join(["0"] * 18) + " 987654 0 0 0"
    (tmp_path / "123" / "stat").write_text(stat)
    monkeypatch.setattr(proc, "_PROC", tmp_path)
    assert proc._pid_start_time(123) == 987654


def test_pid_start_time_missing_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(proc, "_PROC", tmp_path)  # no <pid>/stat under it
    assert proc._pid_start_time(999) is None


def test_pid_start_time_unparseable_is_none(monkeypatch, tmp_path):
    (tmp_path / "5").mkdir()
    (tmp_path / "5" / "stat").write_text("5 (sh) S 1 2 3")  # too few fields
    monkeypatch.setattr(proc, "_PROC", tmp_path)
    assert proc._pid_start_time(5) is None


def test_process_running_start_matches_is_alive(monkeypatch):
    monkeypatch.setattr(proc.os, "waitpid", lambda *_a, **_k: (0, 0))
    monkeypatch.setattr(proc, "_pid_start_time", lambda _pid: 555)
    monkeypatch.setattr(proc.os, "kill", lambda *_a, **_k: None)
    assert _process_running(4242, 555) is True


def test_process_running_pid_recycled_is_gone(monkeypatch):
    monkeypatch.setattr(proc.os, "waitpid", lambda *_a, **_k: (0, 0))
    monkeypatch.setattr(proc, "_pid_start_time", lambda _pid: 999)  # differs
    assert _process_running(4242, 555) is False


def test_process_running_start_unreadable_falls_through(monkeypatch):
    monkeypatch.setattr(proc.os, "waitpid", lambda *_a, **_k: (0, 0))
    monkeypatch.setattr(proc, "_pid_start_time", lambda _pid: None)
    monkeypatch.setattr(proc.os, "kill", lambda *_a, **_k: None)
    assert _process_running(4242, 555) is True
