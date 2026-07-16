"""Shared token check for the web API and log-tail WebSocket."""

import secrets


def token_ok(configured: str | None, supplied: str | None) -> bool:
    """Constant-time token check; open when no token is configured (dev/local)."""
    if configured is None:
        return True
    return supplied is not None and secrets.compare_digest(supplied, configured)
