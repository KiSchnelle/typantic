"""The read/launch HTTP API + a live log-tail WebSocket, over a :class:`Launcher`.

Thin by design: routes call the launcher/store and serialise their pydantic
models. The only "live" surface is the log tail — every job writes one log file
(subprocess capture or a scheduler ``--output``), so tailing is the same across
backends. The built SPA (if present) is served from ``web_dist`` so one process
serves both.
"""

import asyncio
import codecs
import contextlib
from collections.abc import Iterator, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from typantic.web import filesystem, gallery
from typantic.web.backends.scheduler import SchedulerError
from typantic.web.filesystem import FileSystemError
from typantic.web.launcher import (
    JobNotTerminalError,
    Launcher,
    UnknownBackendError,
    UnknownCommandError,
    UnknownProjectError,
)
from typantic.web.models import (
    CommandMeta,
    History,
    JobPage,
    JobRecord,
    JobStatus,
    LaunchPreview,
    LaunchRequest,
    MakeDirRequest,
    Project,
    ProjectCreate,
)
from typantic.web.schema import SchemaError
from typantic.web.security import token_ok

_SPA_DIR = Path(__file__).parent / "web_dist"
_WS_POLICY_VIOLATION = 1008

# Domain error -> HTTP status. Kept in one place so every route that can raise
# them answers the same way; they used to be hand-mapped per route, and had drifted.
_ERROR_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (UnknownCommandError, 404),
    (UnknownBackendError, 400),
    (UnknownProjectError, 400),
    (JobNotTerminalError, 409),
    (SchemaError, 502),
    (SchedulerError, 502),
    (FileSystemError, 400),
    (ValidationError, 422),
)


@contextlib.contextmanager
def _domain_errors() -> Iterator[None]:
    """Translate the launcher's domain errors into HTTP responses."""
    try:
        yield
    except tuple(exc for exc, _ in _ERROR_STATUS) as exc:
        status = next(
            code for kind, code in _ERROR_STATUS if isinstance(exc, kind)
        )
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def make_api(  # noqa: C901, PLR0915 - a route-registering factory; each closure is trivial
    launcher: Launcher,
    *,
    token: str | None = None,
    title: str = "typantic web",
    extra_routers: Sequence[APIRouter] = (),
    dashboard: bool = True,
) -> FastAPI:
    """Build the FastAPI app over ``launcher``.

    Args:
        launcher: The job launcher the routes delegate to.
        token: Shared secret required on ``/api`` and ``/ws`` (via ``Authorization:
            Bearer`` or a ``?token=`` query param). ``None`` disables auth — only
            appropriate for a localhost dev run.
        title: The dashboard brand, surfaced at ``/api/meta``.
        extra_routers: Extra routers to mount (each token-guarded by the caller).
        dashboard: Serve the built SPA at ``/`` if present.

    Returns:
        The configured application (serve with uvicorn).
    """
    app = FastAPI(title=title, version=version("typantic"))

    def require_token(
        authorization: Annotated[str | None, Header()] = None,
        token_q: Annotated[str | None, Query(alias="token")] = None,
    ) -> None:
        supplied = None
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        elif token_q is not None:
            supplied = token_q
        if not token_ok(token, supplied):
            raise HTTPException(status_code=401, detail="Invalid or missing token.")

    guard = [Depends(require_token)]

    @app.get("/api/meta", dependencies=guard)
    def meta() -> dict[str, object]:
        return {
            "title": title,
            "version": version("typantic"),
            "backends": launcher.backends_meta(),
        }

    @app.get("/api/commands", dependencies=guard)
    def list_commands() -> list[CommandMeta]:
        return launcher.commands

    @app.get("/api/commands/{app_name}/{command}/schema", dependencies=guard)
    def command_schema(app_name: str, command: str) -> dict[str, object]:
        with _domain_errors():
            return launcher.schema_for(f"{app_name}/{command}")

    @app.post("/api/launch", dependencies=guard)
    def launch(request: LaunchRequest) -> JobRecord:
        with _domain_errors():
            return launcher.launch(request)

    @app.post("/api/preview", dependencies=guard)
    def preview(request: LaunchRequest) -> LaunchPreview:
        with _domain_errors():
            return launcher.preview(request)

    @app.get("/api/jobs", dependencies=guard)
    def list_jobs(  # noqa: PLR0913 - filter/sort/page query params
        status: Annotated[JobStatus | None, Query()] = None,
        app_name: Annotated[str | None, Query(alias="app")] = None,
        backend: Annotated[str | None, Query()] = None,
        project: Annotated[str | None, Query()] = None,
        ungrouped: Annotated[bool, Query()] = False,
        q: Annotated[str | None, Query()] = None,
        sort: Annotated[str, Query()] = "created_at",
        order: Annotated[str, Query()] = "desc",
        limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JobPage:
        jobs, total = launcher.query(
            status=status,
            app=app_name,
            backend=backend,
            project_id=project,
            ungrouped=ungrouped,
            search=q,
            sort=sort,
            descending=order != "asc",
            limit=limit,
            offset=offset,
        )
        return JobPage(jobs=jobs, total=total)

    @app.get("/api/jobs/{job_id}", dependencies=guard)
    def get_job(job_id: str) -> JobRecord:
        record = launcher.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return record

    @app.post("/api/jobs/{job_id}/cancel", dependencies=guard)
    def cancel_job(job_id: str) -> JobRecord:
        record = launcher.cancel(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return record

    @app.delete("/api/jobs/{job_id}", dependencies=guard)
    def delete_job(job_id: str) -> dict[str, str]:
        if not launcher.delete(job_id):
            raise HTTPException(status_code=404, detail="No such job.")
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/request", dependencies=guard)
    def job_request(job_id: str) -> LaunchRequest:
        request = launcher.request_for(job_id)
        if request is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return request

    @app.post("/api/jobs/{job_id}/restart", dependencies=guard)
    def restart_job(job_id: str, request: LaunchRequest | None = None) -> JobRecord:
        with _domain_errors():
            record = launcher.restart(job_id, request)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return record

    @app.get("/api/jobs/{job_id}/images", dependencies=guard)
    def job_images(job_id: str) -> dict[str, object]:
        record = launcher.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return {"images": gallery.list_images(record, job_id)}

    @app.get("/api/jobs/{job_id}/image", dependencies=guard)
    def job_image(
        job_id: str,
        root: Annotated[int, Query()],
        path: Annotated[str, Query()],
        w: Annotated[
            int | None,
            Query(ge=gallery.THUMB_MIN_WIDTH, le=gallery.THUMB_MAX_WIDTH),
        ] = None,
    ) -> FileResponse:
        record = launcher.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        target = gallery.resolve_artifact(record, root, path)
        if target is None:
            raise HTTPException(status_code=404, detail="No such image.")
        if w is not None:
            thumb = gallery.thumbnail(target, w)
            if thumb is not None:
                return FileResponse(
                    thumb,
                    media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=300"},
                )
        return FileResponse(target)

    @app.get("/api/projects", dependencies=guard)
    def list_projects() -> list[Project]:
        return launcher.store.list_projects()

    @app.post("/api/projects", dependencies=guard)
    def create_project(request: ProjectCreate) -> Project:
        return launcher.store.create_project(request.name, request.description)

    @app.delete("/api/projects/{project_id}", dependencies=guard)
    def delete_project(project_id: str) -> dict[str, str]:
        # Deletes the project AND all its jobs (cancelling any still running).
        if not launcher.delete_project(project_id):
            raise HTTPException(status_code=404, detail="No such project.")
        return {"deleted": project_id}

    @app.get("/api/history", dependencies=guard)
    def history() -> History:
        return launcher.store.grouped_history()

    @app.get("/api/fs", dependencies=guard)
    def browse(path: Annotated[str | None, Query()] = None) -> dict[str, object]:
        return filesystem.browse_directory(path)

    @app.post("/api/fs/mkdir", dependencies=guard)
    def make_dir(request: MakeDirRequest) -> dict[str, object]:
        with _domain_errors():
            return filesystem.make_directory(request.path, request.name)

    @app.websocket("/ws/jobs/{job_id}/log")
    async def stream_log(websocket: WebSocket, job_id: str) -> None:
        if not token_ok(token, websocket.query_params.get("token")):
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        record = launcher.get(job_id)
        if record is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        await websocket.accept()
        await _tail_log(websocket, launcher, job_id, Path(record.log_path))

    for router in extra_routers:
        app.include_router(router)

    if dashboard and _SPA_DIR.is_dir():
        app.mount("/", StaticFiles(directory=_SPA_DIR, html=True), name="spa")

    return app


_LOG_CHUNK_BYTES = 1 << 20
"""Most bytes read (and framed) per tail step, so a huge log streams in pieces."""


def _read_log_from(
    path: Path,
    offset: int,
    decoder: codecs.IncrementalDecoder,
) -> tuple[str, int]:
    """Read up to one chunk of the log from ``offset``.

    Bounded so a multi-gigabyte log is streamed rather than loaded whole: reading
    to EOF would hold the entire file (and a second copy through the JSON frame)
    in memory at once. ``decoder`` carries any partial UTF-8 sequence across the
    chunk boundary, which decoding each chunk independently would corrupt.

    Returns:
        The decoded text and the offset to resume from.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(_LOG_CHUNK_BYTES)
    except OSError:
        return "", offset
    return decoder.decode(data), offset + len(data)


async def _tail_log(
    websocket: WebSocket,
    launcher: Launcher,
    job_id: str,
    log_path: Path,
    *,
    interval: float = 1.0,
) -> None:
    """Stream appended log bytes until the job is terminal, then close.

    Every frame is a JSON envelope (``{"log": …}`` / ``{"end": …}``), so a log
    line can never be mistaken for the end signal.

    This is the only async path in the app -- every route is a sync ``def`` that
    runs in a threadpool -- so its blocking work (reading the log, and a
    ``launcher.get`` that may shell out to a scheduler) is handed to a thread.
    Run inline, one slow ``sacct`` would stall the event loop for every client.
    """
    offset = 0
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            offset = await _drain(websocket, log_path, offset, decoder)
            record = await asyncio.to_thread(launcher.get, job_id)
            if record is None or record.is_terminal:
                await _drain(websocket, log_path, offset, decoder)
                status = record.status if record is not None else "unknown"
                await websocket.send_json({"end": {"status": status}})
                break
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    await websocket.close()


async def _drain(
    websocket: WebSocket,
    log_path: Path,
    offset: int,
    decoder: codecs.IncrementalDecoder,
) -> int:
    """Send every chunk appended since ``offset``; return the new offset."""
    while True:
        text, new_offset = await asyncio.to_thread(
            _read_log_from,
            log_path,
            offset,
            decoder,
        )
        if text:
            await websocket.send_json({"log": text})
        if new_offset == offset:
            return offset
        offset = new_offset
