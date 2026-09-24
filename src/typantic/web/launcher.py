"""The launcher: turn a form submission into a tracked job, and resolve status.

This is the orchestration seam the API and CLI both use. It never imports or
calls an app's ``run()``; it writes the submitted values to
``submit_config.json`` and launches ``<app> <cmd> --config submit_config.json``
through a backend, so the CLI does the authoritative validation and heavy app
dependencies never enter the web process.
"""

import contextlib
import json
import logging
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from typantic.web._files import write_private
from typantic.web.backends import (
    LaunchBackend,
    LaunchUncertainError,
    PollResult,
    load_backends,
)
from typantic.web.discovery import discover_commands
from typantic.web.models import (
    TERMINAL_STATUSES,
    BackendMeta,
    CommandMeta,
    History,
    JobCompat,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    ProjectGroup,
)
from typantic.web.schema import SchemaCache, SchemaError, normalize_for_form
from typantic.web.store import FolderNotRemovedError, JobStore

logger = logging.getLogger("typantic.web")

# Reuse a backend poll for this long so overlapping pollers (jobs list, job
# detail, log WebSocket) don't each shell out for the same running job within
# the same second.
_POLL_TTL_SECONDS = 2.0

# Hard cap on the poll cache. Entries evict on the terminal transition, cancel,
# delete, and project-delete, but a job that terminates without a subsequent
# refresh leaves its entry behind; this bounds that residue to a fixed size.
_POLL_CACHE_MAX = 1024

_PLACEHOLDER_JOB_ID = "<job-id>"

_Run = tuple[str, int | None, int | None, str | None]


def _run(record: JobRecord) -> _Run:
    """What tells one run of a job from the next: a restart reuses the job id."""
    return (record.backend, record.pid, record.pid_start, record.scheduler_id)


def _clean_form_values(values: dict[str, Any]) -> dict[str, Any]:
    """Drop keys the form can only express as "empty", so the model default wins.

    RJSF cannot leave an optional array field *unset* — an untouched array
    submits ``[]``, never omitted. Dropping empty lists lets the settings model
    fall back to its real default rather than pinning the field to ``[]``.

    Nested objects are recursed into, inside arrays too: an array one level
    down is submitted the same way, and stripping only the top level made a
    nested field behave differently from an identical top-level one for no
    reason the user could see.

    Known limit: an empty array is therefore always read as "untouched", so a
    field whose default is non-empty cannot be *cleared* from the form. The
    submission carries no way to tell the two apart; ``--config`` can express it.
    """
    return {
        key: _clean_form_value(value)
        for key, value in values.items()
        if not (isinstance(value, list) and not value)
    }


def _clean_form_value(value: object) -> object:
    if isinstance(value, dict):
        return _clean_form_values(cast("dict[str, Any]", value))
    if isinstance(value, list):
        return [_clean_form_value(item) for item in value]
    return value


def _read_values(config_path: str) -> dict[str, Any]:
    """Load a job's submitted values, tolerating a missing/corrupt config file."""
    try:
        data = json.loads(Path(config_path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


class _Snapshot:
    """A job's settings and log as they were, to put back if a restart fails."""

    def __init__(self, settings: list[Path], log_path: Path) -> None:
        self._settings = {path: _read_or_none(path) for path in settings}
        self._log = log_path
        # The log is moved aside rather than copied: it can be large.
        self._old_log: Path | None = log_path.with_name(f"{log_path.name}.previous")
        try:
            log_path.replace(self._old_log)
        except FileNotFoundError:
            self._old_log = None

    def restore(self) -> None:
        for path, data in self._settings.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                write_private(path, data)
        if self._old_log is None:
            self._log.unlink(missing_ok=True)
        else:
            self._old_log.replace(self._log)

    def discard(self) -> None:
        if self._old_log is not None:
            self._old_log.unlink(missing_ok=True)


def _app_version(app: str) -> str | None:
    """The version of the installed distribution that provides ``app``'s script.

    ``None`` when no distribution in this environment ships that console script,
    e.g. an executable found only on ``PATH``.
    """
    for script in entry_points(group="console_scripts", name=app):
        if script.dist is not None:
            return script.dist.version
    return None


def _stale_settings(values: dict[str, Any], schema: dict[str, object]) -> list[str]:
    """The settings in ``values`` that the command's current schema does not have.

    A setting a newer version of the app renamed or removed, or one an older
    version never had: either way the installed CLI refuses it as unknown.
    Top-level settings only.
    """
    properties = schema.get("properties")
    known: set[str] = set()
    if isinstance(properties, dict):
        known = set(cast("dict[str, object]", properties))
    return sorted(key for key in values if key not in known)


class UnknownCommandError(ValueError):
    """Raised when a launch names a command that is not installed/discovered."""


class StaleSettingsError(ValueError):
    """Raised when settings name one the installed command does not have."""


class UnknownBackendError(ValueError):
    """Raised when a launch names a backend that is not installed."""


class UnknownProjectError(ValueError):
    """Raised when a launch files a job under a project that does not exist."""


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
        # The API serves requests from a thread pool. _lock guards the two dicts
        # below; a job's own lock serialises every change to its stored row.
        self._lock = threading.Lock()
        self._poll_cache: dict[str, tuple[float, _Run, PollResult]] = {}
        self._job_locks: dict[str, threading.RLock] = {}
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

    def backends_meta(self) -> list[BackendMeta]:
        """Each backend's key and its options JSON Schema (for the UI), sorted."""
        meta: list[BackendMeta] = []
        for key in sorted(self._backends):
            model = getattr(self._backends[key], "options_model", None)
            schema = (
                cast("dict[str, Any]", normalize_for_form(model.model_json_schema()))
                if model is not None
                else None
            )
            meta.append(BackendMeta(key=key, options_schema=schema))
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

    def _refuse_stale_settings(self, meta: CommandMeta, values: dict[str, Any]) -> None:
        """Refuse settings the installed command does not have, before anything runs.

        The CLI refuses them too, but only once the job has started -- and a job
        cloned or restarted from another version's settings cannot be fixed in
        the form, which draws only the current settings. When the schema cannot
        be fetched the check is skipped and the CLI's own validation stands.

        Raises:
            StaleSettingsError: If ``values`` holds a setting the command's
                current schema does not have.
        """
        try:
            schema = self.schema_cache.get(meta)
        except SchemaError:
            logger.warning(
                "Could not fetch the schema of %s; launching without checking "
                "its settings.",
                meta.key,
                exc_info=True,
            )
            return
        unknown = _stale_settings(values, schema)
        if unknown:
            noun = "setting" if len(unknown) == 1 else "settings"
            msg = (
                f"The installed {meta.app} has no {meta.command} {noun} "
                f"{', '.join(unknown)}: these settings come from another version "
                f"of it. Start a new job from a freshly loaded form."
            )
            raise StaleSettingsError(msg)

    def compat(self, job_id: str) -> JobCompat | None:
        """Whether a job's settings still fit the installed version of its command.

        ``None`` if the job does not exist.

        Raises:
            UnknownCommandError: If the job's command is no longer installed.
            SchemaError: If the command's schema cannot be fetched.
        """
        record = self.store.load(job_id)
        if record is None:
            return None
        meta = self.command(record.command_key)
        values = self._request_from_record(record).values
        return JobCompat(
            app_version=record.app_version,
            installed_version=_app_version(meta.app),
            unknown_settings=_stale_settings(values, self.schema_cache.get(meta)),
        )

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
        """Launch ``request`` and return the persisted job record.

        Everything that can be rejected is rejected *before* anything is started:
        a process spawned ahead of a failing insert would keep running with no
        record to find, cancel, or clean up by. A job whose row cannot be stored
        even so is stopped again.

        Raises:
            UnknownCommandError: If the command is not installed.
            UnknownBackendError: If the backend is not installed.
            UnknownProjectError: If ``project_id`` names no existing project.
            StaleSettingsError: If the values hold a setting the installed
                command does not have.
        """
        meta = self.command(request.command_key)
        backend = self._backend(request.backend)
        self._check_project(request.project_id)
        self._refuse_stale_settings(meta, request.values)

        created_at = datetime.now(UTC)
        job_id = f"{created_at:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        job_dir = self.store.create_job_dir(job_id)

        try:
            config_path = self.store.config_path(job_id)
            write_private(
                config_path,
                json.dumps(_clean_form_values(request.values), indent=2),
            )
            # The full request so the job can later be cloned or restarted.
            write_private(
                self.store.request_path(job_id),
                request.model_dump_json(indent=2),
            )
            # Created private here, so the backend's writer (a subprocess, or a
            # scheduler's --output) inherits the mode rather than the umask.
            log_path = self.store.log_path(job_id)
            write_private(log_path, "")

            argv = meta.invocation("--config", str(config_path))
            launched = backend.launch(
                argv,
                job_dir=job_dir,
                log_path=log_path,
                backend_options=request.backend_options,
            )
        except LaunchUncertainError:
            raise  # the job may run, and needs its folder
        except Exception:
            # Nothing is running yet (or the backend failed to start it), so the
            # half-built folder is ours to remove rather than leave orphaned.
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

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
            pid_start=launched.pid_start,
            scheduler_id=launched.scheduler_id,
            host=launched.host,
            app_version=_app_version(meta.app),
            status=launched.status,
            created_at=created_at,
        )
        try:
            self._record_started(record, backend)
        except BaseException:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        logger.info("Launched %s as job %s (%s)", meta.key, job_id, request.backend)
        return record

    def _record_started(self, record: JobRecord, backend: LaunchBackend) -> None:
        """Store a just-started job's row, stopping the job again if that fails.

        Unrecorded, a running job could never be found, cancelled or cleaned up.
        """
        try:
            self.store.save(record)
        except BaseException:
            with contextlib.suppress(Exception):
                backend.cancel(record)
            raise

    def _check_project(self, project_id: str | None) -> None:
        """Reject an unknown project before a job is started for it.

        ``jobs.project_id`` is a foreign key, so an unknown one fails at ``save``
        -- by which point the process is already running and untracked.
        """
        if project_id is not None and self.store.get_project(project_id) is None:
            msg = f"Unknown project {project_id!r}."
            raise UnknownProjectError(msg)

    def _job_lock(self, job_id: str) -> threading.RLock:
        """The lock every change to ``job_id``'s stored row is made under.

        Re-entrant, because a cancel or restart resolves the live status through
        :meth:`refresh`, which takes it again to store what it found.
        """
        with self._lock:
            return self._job_locks.setdefault(job_id, threading.RLock())

    def _poll(self, record: JobRecord) -> PollResult:
        """Poll the backend, reusing a recent result for the same run.

        A cached result names the run it was about, so an answer about the run
        before a restart is never handed out for the new one.
        """
        now = time.monotonic()
        with self._lock:
            cached = self._poll_cache.get(record.id)
        if (
            cached is not None
            and cached[1] == _run(record)
            and now - cached[0] < _POLL_TTL_SECONDS
        ):
            return cached[2]
        result = self._backends[record.backend].poll(record)
        with self._lock:
            # Re-insert at the end so eviction is least-recently-updated first.
            self._poll_cache.pop(record.id, None)
            self._poll_cache[record.id] = (now, _run(record), result)
            while len(self._poll_cache) > _POLL_CACHE_MAX:
                del self._poll_cache[next(iter(self._poll_cache))]
        return result

    def _forget_poll(self, job_id: str) -> None:
        with self._lock:
            self._poll_cache.pop(job_id, None)

    def refresh(self, record: JobRecord) -> JobRecord:
        """Re-resolve a non-terminal job's status from its backend and persist it.

        The poll runs unlocked -- a scheduler query can take seconds -- so by the
        time it answers, the job may have been cancelled, restarted or deleted.
        What it found is stored only while the stored row is still the run that
        was polled, and not yet terminal; otherwise the stored row is returned.
        """
        if record.is_terminal or record.backend not in self._backends:
            return record
        result = self._poll(record)
        if result.status == record.status and result.exit_code == record.exit_code:
            return record
        with self._job_lock(record.id):
            current = self.store.load(record.id)
            if current is None:
                return record  # deleted meanwhile: nothing left to update
            if current.is_terminal or _run(current) != _run(record):
                return current  # cancelled, finished or restarted meanwhile
            if (current.status, current.exit_code) == (result.status, result.exit_code):
                return current  # another poll stored this already
            # Every terminal state gets stamped, CANCELLED included: a job
            # cancelled outside the dashboard (scancel, kill) reaches it through
            # this path too, and would otherwise show a finish time of "never".
            # The backend's time wins where it has one (the exit marker's).
            finished_at = current.finished_at
            if result.status in TERMINAL_STATUSES:
                finished_at = result.finished_at or datetime.now(UTC)
            updated = current.model_copy(
                update={
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "finished_at": finished_at,
                },
            )
            if updated.is_terminal:
                self._forget_poll(record.id)
            self.store.save(updated)
            return updated

    def query(  # noqa: PLR0913 - a filter/sort/page query surface
        self,
        *,
        status: JobStatus | None = None,
        app: str | None = None,
        backend: str | None = None,
        project_id: str | None = None,
        ungrouped: bool = False,
        search: str | None = None,
        sort: str = "created_at",
        descending: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        """Query stored jobs (filter/sort/page) and refresh the returned page.

        Filtering is on the *stored* status; a non-terminal job's live status is
        reconciled by the refresh here and by the periodic poll, so a
        status-filtered page can briefly include a just-finished job.
        """
        records, total = self.store.query_jobs(
            status=status,
            app=app,
            backend=backend,
            project_id=project_id,
            ungrouped=ungrouped,
            search=search,
            sort=sort,
            descending=descending,
            limit=limit,
            offset=offset,
        )
        return [self.refresh(record) for record in records], total

    def history(self) -> History:
        """Job history grouped by project, with active jobs' status refreshed."""
        history = self.store.grouped_history()
        return History(
            projects=[
                ProjectGroup(
                    project=group.project,
                    jobs=[self.refresh(job) for job in group.jobs],
                )
                for group in history.projects
            ],
            ungrouped=[self.refresh(job) for job in history.ungrouped],
        )

    def delete_project(self, project_id: str) -> bool:
        """Delete a project and all its jobs, cancelling any still active.

        Raises:
            FolderNotRemovedError: If some job folders could not be removed
                completely; everything else is deleted by then.
        """
        jobs, _ = self.store.query_jobs(project_id=project_id)
        left: list[str] = []
        for record in jobs:
            try:
                self.delete(record.id)
            except FolderNotRemovedError:
                left.append(record.id)
        existed = self.store.delete_project(project_id)
        if left:
            raise FolderNotRemovedError.for_jobs(left)
        return existed

    def get(self, job_id: str) -> JobRecord | None:
        """Return the current (refreshed) record for ``job_id``."""
        record = self.store.load(job_id)
        return self.refresh(record) if record is not None else None

    def cancel(self, job_id: str) -> JobRecord | None:
        """Cancel a job and mark it cancelled; ``None`` if it does not exist.

        The live status is resolved first: a job that has already finished must
        keep its real outcome rather than be recorded CANCELLED forever because
        the stored row had not caught up yet.
        """
        with self._job_lock(job_id):
            # Not the cached poll: a 2 s old RUNNING may describe a job that has
            # finished since, which must keep its outcome.
            self._forget_poll(job_id)
            record = self.get(job_id)
            if record is None:
                return None
            if record.is_terminal:
                return record
            backend = self._backends.get(record.backend)
            if backend is not None:
                try:
                    backend.cancel(record)
                except Exception:
                    # Refused because it has just finished is no failure: show
                    # how it ended. Otherwise it may well still be running.
                    self._forget_poll(job_id)
                    latest = self.refresh(record)
                    if latest.is_terminal:
                        return latest
                    raise
            record = record.model_copy(
                update={
                    "status": JobStatus.CANCELLED,
                    "finished_at": datetime.now(UTC),
                },
            )
            self._forget_poll(job_id)
            self.store.save(record)
            return record

    def delete(self, job_id: str) -> bool:
        """Remove a job entirely, cancelling it first if it is still active."""
        with self._job_lock(job_id):
            record = self.store.load(job_id)
            if record is None:
                return False
            if not record.is_terminal:
                # Asked afresh: a job that has finished since the row was stored
                # must not be signalled -- its pid may name another process now.
                self._forget_poll(job_id)
                record = self.refresh(record)
            if not record.is_terminal:
                self._cancel_before_delete(record)
            self._forget_poll(job_id)
            deleted = self.store.delete(job_id)
        with self._lock:
            # A refresh still waiting on the old lock finds the row gone and
            # stores nothing, so dropping it here is safe.
            self._job_locks.pop(job_id, None)
        return deleted

    def _cancel_before_delete(self, record: JobRecord) -> None:
        """Cancel a job about to be deleted; a failure is logged, not raised.

        Deleting is what was asked for, and the job's row and folder go either
        way -- but a job the backend could not stop may run on, so say so.
        """
        backend = self._backends.get(record.backend)
        if backend is None:
            return
        try:
            backend.cancel(record)
        except Exception:
            logger.warning(
                "Could not cancel job %s before deleting it; it may still be running.",
                record.id,
                exc_info=True,
            )

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
            logger.warning(
                "Job %s has no readable launch request; rebuilding it from the "
                "job and its config, without its backend options.",
                record.id,
            )
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
            StaleSettingsError: If the settings it would run with hold one the
                installed command does not have.
        """
        with self._job_lock(job_id):
            record = self.get(job_id)
            if record is None:
                return None
            if not record.is_terminal:
                msg = (
                    f"Job {job_id} is {record.status.value}; "
                    "only terminal jobs restart."
                )
                raise JobNotTerminalError(msg)

            meta = self.command(record.command_key)

            if request is None:
                new_request = self._request_from_record(record)
            else:
                new_request = request.model_copy(
                    update={"command_key": record.command_key},
                )
            # Validate everything before touching the job's stored settings: a
            # rejected restart must leave the job exactly as it was.
            backend = self._backend(new_request.backend)
            self._check_project(new_request.project_id)
            self._refuse_stale_settings(meta, new_request.values)
            # One set of paths, the store's: a record from a pre-0.8.0 relative
            # jobs root holds relative ones.
            job_dir = self.store.job_dir(job_id)
            config_path = self.store.config_path(job_id)
            request_path = self.store.request_path(job_id)
            log_path = self.store.log_path(job_id)
            argv = meta.invocation("--config", str(config_path))
            # Options the backend would refuse are refused now, not after the
            # new settings have replaced the old.
            backend.preview(
                argv,
                job_dir=job_dir,
                log_path=log_path,
                backend_options=new_request.backend_options,
            )

            snapshot = _Snapshot([config_path, request_path], log_path)
            try:
                if request is not None:
                    write_private(
                        config_path,
                        json.dumps(_clean_form_values(new_request.values), indent=2),
                    )
                    write_private(request_path, new_request.model_dump_json(indent=2))
                # The new run's log starts empty: a queued scheduler job writes
                # nothing until it runs, and showed the old run's log till then.
                write_private(log_path, "")
                self._forget_poll(job_id)
                launched = backend.launch(
                    argv,
                    job_dir=job_dir,
                    log_path=log_path,
                    backend_options=new_request.backend_options,
                )
                restarted = record.model_copy(
                    update={
                        "status": launched.status,
                        "backend": new_request.backend,
                        "name": new_request.name,
                        "project_id": new_request.project_id,
                        "job_dir": str(job_dir),
                        "config_path": str(config_path),
                        "log_path": str(log_path),
                        "pid": launched.pid,
                        "pid_start": launched.pid_start,
                        "scheduler_id": launched.scheduler_id,
                        "host": launched.host,
                        "app_version": _app_version(meta.app),
                        "finished_at": None,
                        "exit_code": None,
                    },
                )
                self._record_started(restarted, backend)
            except LaunchUncertainError:
                snapshot.discard()  # the job may be queued, reading the new files
                raise
            except BaseException:
                snapshot.restore()
                raise
            snapshot.discard()
            logger.info("Restarted job %s (%s)", job_id, restarted.backend)
            return restarted
