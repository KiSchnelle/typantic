"""The read/launch HTTP API + a live log-tail WebSocket, over a :class:`Launcher`.

Thin by design: routes call the launcher/store and serialise their pydantic
models. The only "live" surface is the log tail — every job writes one log file
(subprocess capture or a scheduler ``--output``), so tailing is the same across
backends. The built SPA (if present) is served from ``web_dist`` so one process
serves both.
"""

import asyncio
import os
from collections.abc import Sequence
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

from typantic.web import gallery
from typantic.web.launcher import (
    JobNotTerminalError,
    Launcher,
    UnknownBackendError,
    UnknownCommandError,
)
from typantic.web.models import (
    CommandMeta,
    History,
    JobRecord,
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
        try:
            return launcher.schema_for(f"{app_name}/{command}")
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SchemaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/launch", dependencies=guard)
    def launch(request: LaunchRequest) -> JobRecord:
        try:
            return launcher.launch(request)
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UnknownBackendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/preview", dependencies=guard)
    def preview(request: LaunchRequest) -> LaunchPreview:
        try:
            return launcher.preview(request)
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UnknownBackendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/jobs", dependencies=guard)
    def list_jobs() -> list[JobRecord]:
        return launcher.refresh_all()

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
        try:
            record = launcher.restart(job_id, request)
        except JobNotTerminalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (UnknownCommandError, UnknownBackendError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        if not launcher.store.delete_project(project_id):
            raise HTTPException(status_code=404, detail="No such project.")
        return {"deleted": project_id}

    @app.get("/api/history", dependencies=guard)
    def history() -> History:
        return launcher.store.grouped_history()

    @app.get("/api/fs", dependencies=guard)
    def browse(path: Annotated[str | None, Query()] = None) -> dict[str, object]:
        return _browse_directory(path)

    @app.post("/api/fs/mkdir", dependencies=guard)
    def make_dir(request: MakeDirRequest) -> dict[str, object]:
        return _make_directory(request.path, request.name)

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


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


_BROWSE_ENTRY_CAP = 50000
"""Payload ceiling for one directory listing (the picker virtualises the list)."""


def _browse_directory(path: str | None) -> dict[str, object]:
    """List a directory for the path picker (falls back to home on a bad path)."""
    raw = Path(path).expanduser() if path else Path.home()
    if raw.is_file():
        raw = raw.parent
    if not _is_dir(raw):
        raw = Path.home()
    base = raw.resolve()

    listed: list[tuple[bool, str]] = []
    error: str | None = None
    try:
        with os.scandir(base) as scan:
            for entry in scan:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False
                listed.append((is_dir, entry.name))
    except OSError as exc:
        error = str(exc)

    listed.sort(key=lambda item: (not item[0], item[1].lower()))
    entries = [
        {"name": name, "is_dir": is_dir} for is_dir, name in listed[:_BROWSE_ENTRY_CAP]
    ]
    parent = str(base.parent) if base.parent != base else None
    return {
        "path": str(base),
        "parent": parent,
        "entries": entries,
        "error": error,
        "total": len(listed),
        "truncated": len(listed) > _BROWSE_ENTRY_CAP,
    }


# Reserved / traversal-prone names, plus separators, that must never be a single
# new-folder component. Rejecting these keeps ``parent / name`` inside ``parent``.
_INVALID_DIR_NAMES = frozenset({"", ".", ".."})
_INVALID_DIR_CHARS = frozenset({"/", "\\", "\x00"})


def _make_directory(path: str, name: str) -> dict[str, object]:
    """Create one folder ``name`` under ``path`` and return its (empty) listing."""
    clean = name.strip()
    if clean in _INVALID_DIR_NAMES or _INVALID_DIR_CHARS & set(clean):
        raise HTTPException(status_code=400, detail="Invalid folder name.")
    parent = Path(path).expanduser()
    if not _is_dir(parent):
        raise HTTPException(status_code=400, detail="Parent folder does not exist.")
    target = parent / clean
    try:
        target.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _browse_directory(str(target))


def _read_log_from(path: Path, offset: int) -> tuple[str, int]:
    """Read the log from ``offset``; return the new text and the next offset."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return "", offset
    return data.decode("utf-8", "replace"), offset + len(data)


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
    """
    offset = 0
    try:
        while True:
            text, offset = _read_log_from(log_path, offset)
            if text:
                await websocket.send_json({"log": text})
            record = launcher.get(job_id)
            if record is None or record.is_terminal:
                tail, _ = _read_log_from(log_path, offset)
                if tail:
                    await websocket.send_json({"log": tail})
                status = record.status if record is not None else "unknown"
                await websocket.send_json({"end": {"status": status}})
                break
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    await websocket.close()
