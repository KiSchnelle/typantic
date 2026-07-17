"""Shared token check for the web API and log-tail WebSocket."""

import secrets


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
