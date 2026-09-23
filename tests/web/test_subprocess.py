import signal
import subprocess
import sys

import pytest

from typantic.web import _subprocess


def test_output_that_is_not_utf8_is_decoded_with_replacement():
    result = _subprocess.run_tool(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'caf\\xe9')"],
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == "caf�"


def test_the_exit_code_is_passed_through():
    result = _subprocess.run_tool(
        [sys.executable, "-c", "raise SystemExit(4)"], timeout=30
    )
    assert result.returncode == 4


def test_a_timeout_kills_the_whole_process_group(monkeypatch):
    killed = []
    real_killpg = _subprocess.os.killpg

    def spy(pid, sig):
        killed.append(sig)
        real_killpg(pid, sig)

    monkeypatch.setattr(_subprocess.os, "killpg", spy)
    with pytest.raises(subprocess.TimeoutExpired):
        _subprocess.run_tool(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.2,
        )
    assert killed == [signal.SIGKILL]


def test_a_missing_executable_raises_oserror():
    with pytest.raises(FileNotFoundError):
        _subprocess.run_tool(["/no/such/tool"], timeout=30)
