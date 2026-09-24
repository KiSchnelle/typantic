"""The read/launch HTTP API + a live log-tail WebSocket, over a :class:`Launcher`.

Thin by design: routes call the launcher/store and serialise their pydantic
models. The only "live" surface is the log tail — every job writes one log file
(subprocess capture or a scheduler ``--output``), so tailing is the same across
backends. The built SPA (if present) is served from ``web_dist`` so one process
serves both.
"""

import asyncio
import base64
import codecs
import contextlib
import html
import os
import re
from collections.abc import Iterator, Sequence
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
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import typantic
from typantic.web import filesystem, gallery
from typantic.web.backends.base import ForeignHostError, LaunchUncertainError
from typantic.web.backends.scheduler import SchedulerError
from typantic.web.brand import resolve_brand
from typantic.web.filesystem import FileSystemError
from typantic.web.launcher import (
    JobNotTerminalError,
    Launcher,
    StaleSettingsError,
    UnknownBackendError,
    UnknownCommandError,
    UnknownProjectError,
)
from typantic.web.models import (
    ApiMeta,
    Brand,
    CommandMeta,
    FsListing,
    History,
    JobCompat,
    JobImages,
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
from typantic.web.security import LocalHostOnly, token_ok
from typantic.web.store import FolderNotRemovedError

_SPA_DIR = Path(__file__).parent / "web_dist"
_WS_POLICY_VIOLATION = 1008

# What the SPA's index.html shows in the browser tab: typantic's name and icons,
# which _branded_page replaces with the dashboard's brand.
_PAGE_TITLE = re.compile(r"<title>.*?</title>", re.DOTALL)
_PAGE_ICONS = re.compile(r'\s*<link rel="icon"[^>]*>')

# Domain error -> HTTP status. Kept in one place so every route that can raise
# them answers the same way; they used to be hand-mapped per route, and had drifted.
_ERROR_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (UnknownCommandError, 404),
    (UnknownBackendError, 400),
    (UnknownProjectError, 400),
    (JobNotTerminalError, 409),
    (ForeignHostError, 409),
    (StaleSettingsError, 409),
    (SchemaError, 502),
    (LaunchUncertainError, 504),
    (SchedulerError, 502),
    (FileSystemError, 400),
    (FolderNotRemovedError, 409),
    (ValidationError, 422),
)


@contextlib.contextmanager
def _domain_errors() -> Iterator[None]:
    """Translate the launcher's domain errors into HTTP responses."""
    try:
        yield
    except tuple(exc for exc, _ in _ERROR_STATUS) as exc:
        status = next(code for kind, code in _ERROR_STATUS if isinstance(exc, kind))
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _branded_page(page: str, title: str, icon: str | None) -> str:
    """The SPA's ``index.html`` with the brand's name and icon in the browser tab.

    The page loads with the brand already there: the SPA's own swap, once
    ``/api/meta`` answers, comes too late for Safari, which keeps the icon a page
    loaded with. ``icon`` is the brand's SVG as a data URI, like ``/api/meta``'s,
    so its scripts never run on the dashboard's origin. Without one, typantic's
    icons stay.
    """
    head = f"<title>{html.escape(title)}</title>"
    if icon is not None:
        page = _PAGE_ICONS.sub("", page)
        head += f'\n    <link rel="icon" type="image/svg+xml" href="{icon}" />'
    # A function, not a template: a backslash in a title is not a group reference.
    return _PAGE_TITLE.sub(lambda _: head, page, count=1)


def make_api(  # noqa: C901, PLR0913, PLR0915 - a route-registering factory; each closure is trivial
    launcher: Launcher,
    *,
    token: str | None = None,
    title: str | None = None,
    extra_routers: Sequence[APIRouter] = (),
    dashboard: bool = True,
    host: str | None = None,
    brand: Brand | None = None,
) -> FastAPI:
    """Build the FastAPI app over ``launcher``.

    Args:
        launcher: The job launcher the routes delegate to.
        token: Shared secret required on ``/api`` and ``/ws`` (via ``Authorization:
            Bearer`` or a ``?token=`` query param). ``None`` disables auth — only
            appropriate for a localhost dev run — and then serves only requests
            addressed to a loopback name or ``host`` (see
            :class:`~typantic.web.security.LocalHostOnly`).
        title: The dashboard's name; overrides the brand's.
        extra_routers: Extra routers to mount (each token-guarded by the caller).
        dashboard: Serve the built SPA at ``/`` if present, the brand already in
            its browser tab.
        host: The host the server is bound to, served without a token as well.
        brand: How the dashboard presents itself (typantic's own when ``None``),
            surfaced at ``/api/meta`` and in the page's browser tab. Never
            discovered here: see :mod:`typantic.web.brand`.

    Returns:
        The configured application (serve with uvicorn).
    """
    shown = resolve_brand(brand, title=title)
    icon = (
        f"data:image/svg+xml;base64,{base64.b64encode(shown.icon.encode()).decode()}"
        if shown.icon is not None
        else None
    )
    app = FastAPI(title=shown.title, version=typantic.__version__)
    if token is None:
        app.add_middleware(LocalHostOnly, hosts=[host] if host else [])

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
    def meta() -> ApiMeta:
        return ApiMeta(
            title=shown.title,
            version=typantic.__version__,
            backends=launcher.backends_meta(),
            wordmark_lead=shown.lead,
            wordmark_rest=shown.rest,
            icon=icon,
            accent=shown.accent,
        )

    @app.get("/api/commands", dependencies=guard)
    def list_commands() -> list[CommandMeta]:
        return launcher.commands

    @app.post("/api/commands/refresh", dependencies=guard)
    def refresh_commands() -> list[CommandMeta]:
        # Re-discover installed apps so a newly pip-installed command appears
        # without restarting the server.
        return launcher.refresh_commands()

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
    def list_jobs(  # noqa: PLR0913, PLR0917 - filter/sort/page query params
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
        with _domain_errors():
            record = launcher.cancel(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return record

    @app.delete("/api/jobs/{job_id}", dependencies=guard)
    def delete_job(job_id: str) -> dict[str, str]:
        with _domain_errors():
            deleted = launcher.delete(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="No such job.")
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/request", dependencies=guard)
    def job_request(job_id: str) -> LaunchRequest:
        request = launcher.request_for(job_id)
        if request is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return request

    @app.get("/api/jobs/{job_id}/compat", dependencies=guard)
    def job_compat(job_id: str) -> JobCompat:
        with _domain_errors():
            compat = launcher.compat(job_id)
        if compat is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return compat

    @app.post("/api/jobs/{job_id}/restart", dependencies=guard)
    def restart_job(job_id: str, request: LaunchRequest | None = None) -> JobRecord:
        with _domain_errors():
            record = launcher.restart(job_id, request)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return record

    @app.get("/api/jobs/{job_id}/images", dependencies=guard)
    def job_images(job_id: str) -> JobImages:
        record = launcher.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return gallery.list_images(record, job_id)

    @app.get("/api/jobs/{job_id}/image", dependencies=guard)
    def job_image(
        job_id: str,
        root: Annotated[int, Query()],
        path: Annotated[str, Query()],
        w: Annotated[
            int | None,
            Query(ge=gallery.THUMB_MIN_WIDTH, le=gallery.THUMB_MAX_WIDTH),
        ] = None,
    ) -> Response:
        record = launcher.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such job.")
        target = gallery.resolve_artifact(record, root, path)
        if target is None:
            raise HTTPException(status_code=404, detail="No such image.")
        if w is None:
            return FileResponse(target)
        thumb = gallery.thumbnail(target, w)
        if thumb is None:
            # Not the original instead: it may be hundreds of megabytes, which is
            # what a thumbnail exists to avoid. The tile shows the file's name.
            raise HTTPException(status_code=415, detail="No thumbnail for this image.")
        # The URL carries the source's mtime and the width, so a given URL
        # always means the same thumbnail.
        return Response(
            thumb,
            media_type="image/webp",
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @app.get("/api/projects", dependencies=guard)
    def list_projects() -> list[Project]:
        return launcher.store.list_projects()

    @app.post("/api/projects", dependencies=guard)
    def create_project(request: ProjectCreate) -> Project:
        return launcher.store.create_project(request.name, request.description)

    @app.delete("/api/projects/{project_id}", dependencies=guard)
    def delete_project(project_id: str) -> dict[str, str]:
        # Deletes the project AND all its jobs (cancelling any still running).
        with _domain_errors():
            deleted = launcher.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="No such project.")
        return {"deleted": project_id}

    @app.get("/api/history", dependencies=guard)
    def history() -> History:
        return launcher.history()

    @app.get("/api/fs", dependencies=guard)
    def browse(path: Annotated[str | None, Query()] = None) -> FsListing:
        return filesystem.browse_directory(path)

    @app.post("/api/fs/mkdir", dependencies=guard)
    def make_dir(request: MakeDirRequest) -> FsListing:
        with _domain_errors():
            return filesystem.make_directory(request.path, request.name)

    @app.websocket("/ws/jobs/{job_id}/log")
    async def stream_log(websocket: WebSocket, job_id: str) -> None:
        if not token_ok(token, websocket.query_params.get("token")):
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        # Off the event loop: a scheduler job's status is a sacct call away.
        record = await asyncio.to_thread(launcher.get, job_id)
        if record is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        await websocket.accept()
        await _tail_log(websocket, launcher, job_id, Path(record.log_path))

    for router in extra_routers:
        app.include_router(router)

    if dashboard and _SPA_DIR.is_dir():
        # Ahead of the static mount, which would serve index.html as built: with
        # typantic's name and icons in the tab (see _branded_page).
        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
        def page() -> HTMLResponse:
            # Read per request, like the static files: a rebuilt SPA needs no restart.
            try:
                built = (_SPA_DIR / "index.html").read_text(encoding="utf-8")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="Not Found") from None
            return HTMLResponse(_branded_page(built, shown.title, icon))

        app.mount("/", StaticFiles(directory=_SPA_DIR, html=True), name="spa")

    return app


_LOG_CHUNK_BYTES = 1 << 20
"""Most bytes read (and framed) per tail step, so a huge log streams in pieces."""


class _LogCursor:
    """How far a tail has read a job's log, and which file that was.

    A restart in place gives the job a fresh log file; a tail that kept its
    offset would skip the new run's first bytes (or all of them). So each read
    checks the file is still the one read so far, and not shorter than the
    offset, and otherwise starts again from the top.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self._file: tuple[int, int] | None = None  # (device, inode) read so far
        # Carries a partial UTF-8 sequence across chunk boundaries, which
        # decoding each chunk independently would corrupt.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def read(self) -> tuple[str, bool]:
        """Up to one chunk of new text, and whether the log started over first.

        Bounded so a multi-gigabyte log is streamed rather than loaded whole:
        reading to EOF would hold the entire file (and a second copy through
        the JSON frame) in memory at once.
        """
        try:
            with self.path.open("rb") as handle:
                info = os.fstat(handle.fileno())
                current = (info.st_dev, info.st_ino)
                restarted = self._file is not None and (
                    current != self._file or info.st_size < self.offset
                )
                if restarted:
                    self.offset = 0
                    self._decoder.reset()
                self._file = current
                handle.seek(self.offset)
                data = handle.read(_LOG_CHUNK_BYTES)
        except OSError:
            return "", False
        self.offset += len(data)
        return self._decoder.decode(data), restarted


async def _tail_log(
    websocket: WebSocket,
    launcher: Launcher,
    job_id: str,
    log_path: Path,
    *,
    interval: float = 1.0,
) -> None:
    """Stream appended log bytes until the job is terminal, then close.

    Every frame is a JSON envelope -- ``{"log": …}``, ``{"reset": true}`` when
    the log started over (the client clears what it has), ``{"end": …}`` -- so a
    log line can never be mistaken for a signal.

    This is the only async path in the app -- every route is a sync ``def`` that
    runs in a threadpool -- so its blocking work (reading the log, and a
    ``launcher.get`` that may shell out to a scheduler) is handed to a thread.
    Run inline, one slow ``sacct`` would stall the event loop for every client.
    The wait between polls listens for the client leaving: a quiet job sends
    nothing, and a closed socket is otherwise only noticed on a send.
    """
    cursor = _LogCursor(log_path)
    try:
        while True:
            await _drain(websocket, cursor)
            record = await asyncio.to_thread(launcher.get, job_id)
            if record is None or record.is_terminal:
                await _drain(websocket, cursor)
                status = record.status if record is not None else "unknown"
                await websocket.send_json({"end": {"status": status}})
                break
            if await _client_left(websocket, within=interval):
                return
    except WebSocketDisconnect:
        return
    await websocket.close()


async def _client_left(websocket: WebSocket, *, within: float) -> bool:
    """Wait up to ``within`` seconds for the client to leave; whether it did."""
    try:
        async with asyncio.timeout(within):
            message = await websocket.receive()
    except TimeoutError:
        return False
    kind: str = message["type"]
    return kind == "websocket.disconnect"


async def _drain(websocket: WebSocket, cursor: _LogCursor) -> None:
    """Send every chunk appended since the cursor, and a reset if it started over."""
    while True:
        offset = cursor.offset
        text, restarted = await asyncio.to_thread(cursor.read)
        if restarted:
            await websocket.send_json({"reset": True})
        if text:
            await websocket.send_json({"log": text})
        if cursor.offset == offset and not restarted:
            return
