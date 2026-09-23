"""Writing the job store's files so only their owner can read them.

A job's submitted config can carry a secret the form took as plain text, and its
log whatever the command printed. On a shared login node the usual 022 umask
made both readable by every user. The store's folders are therefore created
0700 and the files typantic writes into them 0600 -- including a file an older
version left readable, which is reset whenever it is rewritten.
"""

import os
from pathlib import Path

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8, readable and writable by its owner only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        # O_CREAT's mode applies only to a new file; an existing one keeps its own.
        os.fchmod(out.fileno(), PRIVATE_FILE)
        out.write(text)


def touch_private(path: Path) -> None:
    """Create ``path`` empty and owner-only, leaving an existing file untouched."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, PRIVATE_FILE))
