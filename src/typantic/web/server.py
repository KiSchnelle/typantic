"""Serve the dashboard as the invoking user, Jupyter-style (free port + token).

Running as the Unix user who launches it means file access and scheduler
accounting inherit their identity — no central auth, no impersonation. A random
token in a localhost URL is the credential; the user reaches it over an SSH
tunnel.
"""

import secrets
import socket

from typantic.web.api import make_api
from typantic.web.launcher import Launcher


def find_free_port(host: str) -> int:
    """Pick a free ephemeral port on ``host`` (the Jupyter pattern)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def resolve_token(token: str | None, *, disable: bool) -> str | None:
    """Return the token to enforce: explicit, else generated, unless disabled."""
    if disable:
        return None
    return token or secrets.token_urlsafe(24)


def dashboard_url(host: str, port: int, token: str | None) -> str:
    """Build the localhost URL a user opens, embedding the token when set."""
    base = f"http://{host}:{port}/"
    return f"{base}?token={token}" if token else base


def serve(
    launcher: Launcher,
    *,
    host: str,
    port: int,
    token: str | None,
    title: str = "typantic web",
    log_level: str = "info",
) -> None:
    """Run the dashboard server in the foreground (blocks until interrupted)."""
    import uvicorn  # noqa: PLC0415 - deferred so --help/--version stay light

    app = make_api(launcher, token=token, title=title)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
