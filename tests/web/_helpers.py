"""Test doubles and helpers shared by the web test modules."""

import contextlib
import os
import stat

import pytest

from typantic.web import schema as schema_mod
from typantic.web.backends.base import Launched, PollResult
from typantic.web.models import JobStatus


class FakeBackend:
    def __init__(self):
        self.launched = []
        self.cancelled = []
        self.poll_count = 0
        self.host = None
        self.next_status = JobStatus.RUNNING
        self.poll_result = PollResult(status=JobStatus.RUNNING)
        # Runs in place of the poll, to act out what happens while a slow
        # status query (sacct can take seconds) is in flight.
        self.on_poll = None
        # A scheduler job writes its log only once it starts running.
        self.writes_log = True
        self.rejects = None  # an exception preview() and launch() raise
        self.cancel_error = None  # an exception cancel() raises

    def launch(self, argv, *, job_dir, log_path, backend_options):
        if self.rejects is not None:
            raise self.rejects
        pid = 4321 + len(self.launched)  # each run gets its own process
        self.launched.append((argv, backend_options))
        if self.writes_log:
            log_path.write_text("hello\n")
        return Launched(pid=pid, status=self.next_status, host=self.host)

    def poll(self, record):
        self.poll_count += 1
        if self.on_poll is not None:
            return self.on_poll(record)
        return self.poll_result

    def cancel(self, record):
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(record.id)

    def preview(self, argv, *, job_dir, log_path, backend_options):
        if self.rejects is not None:
            raise self.rejects
        return "PREVIEW"


def installed_settings(monkeypatch, launcher, *names):
    """The installed command's form has exactly these settings."""
    schema = {"type": "object", "properties": {name: {} for name in names}}
    monkeypatch.setattr(schema_mod, "fetch_schema", lambda _meta: schema)
    launcher.schema_cache.clear()  # as installing that version would


def file_mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@contextlib.contextmanager
def locked_folder(*folders):
    """Folders nothing inside can be removed from; skips as root, who ignores that."""
    if os.geteuid() == 0:
        pytest.skip("root can remove anything")
    for folder in folders:
        folder.chmod(0o500)
    try:
        yield
    finally:
        for folder in folders:
            folder.chmod(0o700)
