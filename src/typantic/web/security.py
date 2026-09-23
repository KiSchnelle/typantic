"""Shared request checks for the web API and log-tail WebSocket."""

import ipaddress
import secrets
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_WS_POLICY_VIOLATION = 1008


def token_ok(configured: str | None, supplied: str | None) -> bool:
    """Constant-time token check; open when no token is configured (dev/local).

    The comparison is on the encoded bytes: ``compare_digest`` rejects a ``str``
    holding non-ASCII outright, so a token with an umlaut in it would raise
    ``TypeError`` and turn an ordinary 401 into a 500.
    """
    if configured is None:
        return True
    if supplied is None:
        return False
    return secrets.compare_digest(supplied.encode(), configured.encode())


class LocalHostOnly:
    """ASGI middleware serving only requests addressed to this machine by name.

    Without a token the API trusts whoever can connect, and a DNS-rebinding page
    can: ``evil.example`` re-resolves its own name to ``127.0.0.1``, and its
    script then calls the API from the victim's browser, same-origin. The
    ``Host`` header is the one thing that still names ``evil.example``. So HTTP
    requests and WebSocket handshakes are served only for a loopback name
    (``localhost``, ``127.x.x.x``, ``[::1]``) or one of ``hosts``.

    Starlette's ``TrustedHostMiddleware`` splits ``[::1]:8000`` at its first
    colon, hence a parser of its own.
    """

    def __init__(self, app: ASGIApp, *, hosts: Iterable[str] = ()) -> None:
        """Wrap ``app``; ``hosts`` are further names to serve (the bound host)."""
        self.app = app
        self.hosts = frozenset(host.lower() for host in hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass a request addressed here to the app; refuse any other."""
        if scope["type"] not in ("http", "websocket") or self._addressed_here(scope):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await receive()  # the handshake's websocket.connect
            # Closing before accepting rejects the handshake (HTTP 403).
            await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
        else:
            refusal = PlainTextResponse("Invalid Host header.", status_code=400)
            await refusal(scope, receive, send)

    def _addressed_here(self, scope: Scope) -> bool:
        header = Headers(scope=scope).get("host")
        name = host_name(header) if header else None
        return name is not None and (_is_loopback(name) or name.lower() in self.hosts)


def host_name(header: str) -> str | None:
    """The name in a ``Host`` header without its port; ``None`` if malformed.

    ``[::1]:8000`` is ``::1``, and ``localhost:8000`` is ``localhost``. Parsed
    strictly: ``[::1].evil.example`` is not an address in brackets.
    """
    if header.startswith("["):
        literal, bracket, rest = header[1:].partition("]")
        if not bracket or (rest and not _is_port(rest)):
            return None
        return literal
    name, colon, port = header.rpartition(":")
    if not colon:
        return header
    if ":" in name or not port.isdigit():
        return None  # an unbracketed IPv6 literal, or a malformed port
    return name


def _is_port(suffix: str) -> bool:
    return suffix.startswith(":") and suffix[1:].isdigit()


def _is_loopback(name: str) -> bool:
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False
