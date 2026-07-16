"""The launcher: turn a form submission into a tracked job, and resolve status.

This is the orchestration seam the API and CLI both use. It never imports or
calls an app's ``run()``; it writes the submitted values to
``submit_config.json`` and launches ``<app> <cmd> --config submit_config.json``
through a backend, so the CLI does the authoritative validation and heavy app
dependencies never enter the web process.
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from typantic.web.backends import LaunchBackend, PollResult, load_backends
from typantic.web.discovery import discover_commands
from typantic.web.models import (
    CommandMeta,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
)
from typantic.web.schema import SchemaCache, normalize_for_form
from typantic.web.store import JobStore

logger = logging.getLogger("typantic.web")

# Reuse a backend poll for this long so overlapping pollers (jobs list, job
# detail, log WebSocket) don't each shell out for the same running job within
# the same second.
_POLL_TTL_SECONDS = 2.0

_PLACEHOLDER_JOB_ID = "<job-id>"


def _clean_form_values(values: dict[str, Any]) -> dict[str, Any]:
    """Drop keys the form can only express as "empty", so the model default wins.

    RJSF cannot leave an optional array field *unset* — an untouched array
    submits ``[]``, never omitted. Dropping empty lists lets the settings model
    fall back to its real default rather than pinning the field to ``[]``.
    """
    return {
        key: value
        for key, value in values.items()
        if not (isinstance(value, list) and not value)
    }


def _read_values(config_path: str) -> dict[str, Any]:
    """Load a job's submitted values, tolerating a missing/corrupt config file."""
    try:
        data = json.loads(Path(config_path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class UnknownCommandError(ValueError):
    """Raised when a launch names a command that is not installed/discovered."""


class UnknownBackendError(ValueError):
    """Raised when a launch names a backend that is not installed."""


class JobNotTerminalError(RuntimeError):
    """Raised when a still-running job is asked to restart (must be terminal)."""


class Launcher:
    """Launch jobs and keep their records' status current."""

    def __init__(
        self,
        store: JobStore,
        *,
        schema_cache: SchemaCache | None = None,
        backends: dict[str, LaunchBackend] | None = None,
    ) -> None:
        """Wire the launcher to a store and (optionally) custom backends/cache."""
        self.store = store
        self.schema_cache = schema_cache or SchemaCache()
        self._backends = backends if backends is not None else load_backends()
        self._poll_cache: dict[str, tuple[float, PollResult]] = {}
        self.refresh_commands()

    def refresh_commands(self) -> list[CommandMeta]:
        """Re-discover installed commands (e.g. after installing a new app)."""
        self._commands = discover_commands()
        self._by_key = {meta.key: meta for meta in self._commands}
        self.schema_cache.clear()
        return self._commands

    @property
    def commands(self) -> list[CommandMeta]:
        """The discovered launchable commands."""
        return self._commands

    @property
    def backend_keys(self) -> list[str]:
        """The keys of the installed launch backends, sorted."""
        return sorted(self._backends)

    def backends_meta(self) -> list[dict[str, object]]:
        """Each backend's key and its options JSON Schema (for the UI), sorted."""
        meta: list[dict[str, object]] = []
        for key in sorted(self._backends):
            model = getattr(self._backends[key], "options_model", None)
            schema = (
                normalize_for_form(model.model_json_schema())
                if model is not None
                else None
            )
            meta.append({"key": key, "options_schema": schema})
        return meta

    def command(self, key: str) -> CommandMeta:
        """Look up a command by key, raising :class:`UnknownCommandError`."""
        try:
            return self._by_key[key]
        except KeyError as exc:
            msg = f"Unknown command {key!r}."
            raise UnknownCommandError(msg) from exc

    def _backend(self, key: str) -> LaunchBackend:
        """Look up a backend by key, raising :class:`UnknownBackendError`."""
        try:
            return self._backends[key]
        except KeyError as exc:
            msg = f"Unknown backend {key!r}."
            raise UnknownBackendError(msg) from exc

    def schema_for(self, key: str) -> dict[str, object]:
        """Return the JSON Schema for a command's form."""
        return self.schema_cache.get(self.command(key))

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        """Dry-run a launch: the config and the command/script that would run."""
        meta = self.command(request.command_key)
        backend = self._backend(request.backend)
        placeholder = self.store.root / _PLACEHOLDER_JOB_ID
        config_path = placeholder / "submit_config.json"
        argv = meta.invocation("--config", str(config_path))
        config = json.dumps(_clean_form_values(request.values), indent=2)
        script = backend.preview(
            argv,
            job_dir=placeholder,
            log_path=placeholder / "job.log",
            backend_options=request.backend_options,
        )
        return LaunchPreview(config=config, argv=argv, script=script)

    def launch(self, request: LaunchRequest) -> JobRecord:
        """Launch ``request`` and return the persisted job record."""
        meta = self.command(request.command_key)
        backend = self._backend(request.backend)

        created_at = datetime.now(UTC)
        job_id = f"{created_at:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        job_dir = self.store.create_job_dir(job_id)

        config_path = self.store.config_path(job_id)
        config_path.write_text(json.dumps(_clean_form_values(request.values), indent=2))
        # The full request so the job can later be cloned or restarted.
        self.store.request_path(job_id).write_text(request.model_dump_json(indent=2))
        log_path = self.store.log_path(job_id)

        argv = meta.invocation("--config", str(config_path))
        launched = backend.launch(
            argv,
            job_dir=job_dir,
            log_path=log_path,
            backend_options=request.backend_options,
        )
        record = JobRecord(
            id=job_id,
            command_key=meta.key,
            app=meta.app,
            command=meta.command,
            title=meta.title,
            name=request.name,
            project_id=request.project_id,
            backend=request.backend,
            job_dir=str(job_dir),
            config_path=str(config_path),
            log_path=str(log_path),
            pid=launched.pid,
            scheduler_id=launched.scheduler_id,
            status=launched.status,
            created_at=created_at,
        )
        self.store.save(record)
        logger.info("Launched %s as job %s (%s)", meta.key, job_id, request.backend)
        return record

    def _poll(self, record: JobRecord) -> PollResult:
        """Poll the backend, reusing a recent result within the TTL window."""
        now = time.monotonic()
        cached = self._poll_cache.get(record.id)
        if cached is not None and now - cached[0] < _POLL_TTL_SECONDS:
            return cached[1]
        result = self._backends[record.backend].poll(record)
        self._poll_cache[record.id] = (now, result)
        return result

    def refresh(self, record: JobRecord) -> JobRecord:
        """Re-resolve a non-terminal job's status from its backend and persist it."""
        if record.is_terminal or record.backend not in self._backends:
            return record
        result = self._poll(record)
        if result.status == record.status and result.exit_code == record.exit_code:
            return record
        # refresh only runs on non-terminal records, so finished_at is None here.
        finished_at = (
            datetime.now(UTC)
            if result.status in {JobStatus.DONE, JobStatus.FAILED}
            else record.finished_at
        )
        record = record.model_copy(
            update={
                "status": result.status,
                "exit_code": result.exit_code,
                "finished_at": finished_at,
            },
        )
        if record.is_terminal:
            self._poll_cache.pop(record.id, None)
        self.store.save(record)
        return record

    def refresh_all(self) -> list[JobRecord]:
        """Refresh every stored job's status, newest first."""
        return [self.refresh(record) for record in self.store.list_records()]

    def get(self, job_id: str) -> JobRecord | None:
        """Return the current (refreshed) record for ``job_id``."""
        record = self.store.load(job_id)
        return self.refresh(record) if record is not None else None

    def cancel(self, job_id: str) -> JobRecord | None:
        """Cancel a job and mark it cancelled; ``None`` if it does not exist."""
        record = self.store.load(job_id)
        if record is None:
            return None
        if record.is_terminal:
            return record
        backend = self._backends.get(record.backend)
        if backend is not None:
            backend.cancel(record)
        record = record.model_copy(
            update={"status": JobStatus.CANCELLED, "finished_at": datetime.now(UTC)},
        )
        self._poll_cache.pop(job_id, None)
        self.store.save(record)
        return record

    def delete(self, job_id: str) -> bool:
        """Remove a job entirely, cancelling it first if it is still active."""
        record = self.store.load(job_id)
        if record is None:
            return False
        if not record.is_terminal:
            backend = self._backends.get(record.backend)
            if backend is not None:
                backend.cancel(record)
        self._poll_cache.pop(job_id, None)
        return self.store.delete(job_id)

    def request_for(self, job_id: str) -> LaunchRequest | None:
        """The launch request behind a job, for cloning or restarting it."""
        record = self.store.load(job_id)
        if record is None:
            return None
        return self._request_from_record(record)

    def _request_from_record(self, record: JobRecord) -> LaunchRequest:
        """Reload a job's full launch request, reconstructing it if not stored."""
        try:
            return LaunchRequest.model_validate_json(
                self.store.request_path(record.id).read_text(),
            )
        except (OSError, ValidationError):
            return LaunchRequest(
                command_key=record.command_key,
                backend=record.backend,
                name=record.name,
                project_id=record.project_id,
                values=_read_values(record.config_path),
            )

    def restart(
        self,
        job_id: str,
        request: LaunchRequest | None = None,
    ) -> JobRecord | None:
        """Re-run a terminal job in place, optionally with edited settings.

        Re-launches under the same job id, so the jobs list keeps one entry that
        restarts. Without ``request`` the job's original settings are reused. The
        command is fixed by the job being restarted, so a ``command_key`` in
        ``request`` is ignored. ``None`` if the job does not exist.

        Raises:
            JobNotTerminalError: If the job is still active.
        """
        record = self.get(job_id)
        if record is None:
            return None
        if not record.is_terminal:
            msg = f"Job {job_id} is {record.status.value}; only terminal jobs restart."
            raise JobNotTerminalError(msg)

        meta = self.command(record.command_key)
        self._poll_cache.pop(job_id, None)

        if request is None:
            new_request = self._request_from_record(record)
        else:
            new_request = request.model_copy(
                update={"command_key": record.command_key},
            )
            self.store.config_path(job_id).write_text(
                json.dumps(_clean_form_values(new_request.values), indent=2),
            )
            self.store.request_path(job_id).write_text(
                new_request.model_dump_json(indent=2),
            )

        backend = self._backend(new_request.backend)
        argv = meta.invocation("--config", record.config_path)
        launched = backend.launch(
            argv,
            job_dir=Path(record.job_dir),
            log_path=Path(record.log_path),
            backend_options=new_request.backend_options,
        )
        record = record.model_copy(
            update={
                "status": launched.status,
                "backend": new_request.backend,
                "name": new_request.name,
                "project_id": new_request.project_id,
                "pid": launched.pid,
                "scheduler_id": launched.scheduler_id,
                "finished_at": None,
                "exit_code": None,
            },
        )
        self.store.save(record)
        logger.info("Restarted job %s (%s)", job_id, record.backend)
        return record
