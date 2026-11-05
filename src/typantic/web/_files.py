"""Writing the job store's files so only their owner can read them.

A job's submitted config can carry a secret the form took as plain text, and its
log whatever the command printed. On a shared login node the usual 022 umask
would make both readable by every user. The store's folders are therefore
created 0700 and the files typantic writes into them 0600. An existing file's
mode is reset whenever it is rewritten.
"""

import json
import os
from pathlib import Path
from typing import Any

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


def write_private(path: Path, data: str | bytes) -> None:
    """Write ``data`` (text as UTF-8) to ``path``, readable by its owner only."""
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    with os.fdopen(fd, "wb") as out:
        # O_CREAT's mode applies only to a new file; an existing one keeps its own.
        os.fchmod(out.fileno(), PRIVATE_FILE)
        out.write(raw)


def touch_private(path: Path) -> None:
    """Create ``path`` empty and owner-only, leaving an existing file untouched."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, PRIVATE_FILE))


def read_json_mapping(path: Path) -> dict[str, Any] | None:
    """The JSON object in ``path``, or ``None`` if it is unreadable or not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    # ValueError covers JSON and decoding errors, RecursionError absurd nesting.
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None
