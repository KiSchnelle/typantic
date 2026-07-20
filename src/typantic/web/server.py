"""Serve the dashboard as the invoking user, Jupyter-style (free port + token).

Running as the Unix user who launches it means file access and scheduler
accounting inherit their identity — no central auth, no impersonation. A random
token in a localhost URL is the credential; the user reaches it over an SSH
tunnel.
"""

import ipaddress
import secrets
import socket
from urllib.parse import quote

from typantic.web.api import make_api
from typantic.web.launcher import Launcher


def find_free_port(host: str) -> int:
    """Pick a free ephemeral port on ``host`` (the Jupyter pattern)."""
    family = socket.AF_INET6 if _is_ipv6(host) else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _is_ipv6(host: str) -> bool:
    """Whether ``host`` is an IPv6 literal (``::1``, ``fe80::1`` …)."""
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False  # a hostname, not a literal


def resolve_token(token: str | None, *, disable: bool) -> str | None:
    """Return the token to enforce: explicit, else generated, unless disabled."""
    if disable:
        return None
    return token or secrets.token_urlsafe(24)


def dashboard_url(host: str, port: int, token: str | None) -> str:
    """Build the localhost URL a user opens, embedding the token when set.

    The token is percent-encoded (an explicit ``--token`` may hold ``+``, ``&`` or
    ``#``, which would otherwise silently truncate or corrupt the query), and an
    IPv6 literal host is bracketed so the port stays readable as a port.
    """
    netloc = f"[{host}]" if _is_ipv6(host) else host
    base = f"http://{netloc}:{port}/"
    return f"{base}?token={quote(token, safe='')}" if token else base


def local_server_name() -> str:
    """Best-effort name to SSH back to this host, for the tunnel command.

    ``getfqdn`` resolves to a routable name on most clusters, but can return a
    ``localhost`` alias on hosts whose loopback resolves first; there, fall back
    to the bare node name. It is only a starting point the user can edit.
    """
    fqdn = socket.getfqdn()
    if not fqdn or fqdn.startswith("localhost"):
        return socket.gethostname()
    return fqdn


def ssh_forward_command(host: str, port: int, *, user: str, server: str) -> str:
    """The ``ssh -N -L ...`` line that tunnels the dashboard port to a laptop.

    ``user`` and ``server`` are the serving host's own login and name, so the
    line is copy-paste ready; edit them if you reach the host under a different
    name.
    """
    return f"ssh -N -L {port}:{host}:{port} {user}@{server}"


def startup_banner(
    *,
    title: str,
    host: str,
    port: int,
    token: str | None,
    user: str,
    server: str,
) -> list[str]:
    """Assemble the copy-paste startup banner printed by ``serve``.

    Shows the URL to open, then a ready-to-run ``ssh -N -L`` line (with this
    host's user and name filled in) for reaching the dashboard from a remote
    machine. The token note is omitted when the server runs without a token.
    """
    url = dashboard_url(host, port, token)
    ssh_command = ssh_forward_command(host, port, user=user, server=server)
    lines = [
        "",
        f"  {title} is running.",
        "",
        "  Open in a browser:",
        f"    {url}",
        "",
        "  Remote host? First forward the port from your machine:",
        f"    {ssh_command}",
        "  then open the URL above locally.",
    ]
    if token is not None:
        lines += [
            "",
            "  The token in the URL is the credential; keep it private.",
        ]
    lines.append("")
    return lines


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
