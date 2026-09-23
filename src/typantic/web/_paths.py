"""Path checks for the path picker and the gallery that answer instead of raising.

The dashboard's path handling runs on paths a user typed or a job's config
names, so a check must answer, never crash the request. ``Path.is_file()`` /
``is_dir()`` on Python 3.12 and 3.13 re-raise any error other than "missing" (a
permission error under someone else's 0700 home, a component longer than 255
bytes) where 3.14 returns ``False``; 3.12's ``resolve()`` raises on a symlink
loop; ``expanduser()`` raises for an unknown user (``~results`` meant as
``~/results``). ``os.path.isfile`` / ``isdir`` swallow ``OSError`` and
``ValueError`` on every version, and the helpers below guard the rest.
"""

import os
from pathlib import Path


def is_file(path: Path) -> bool:
    """Whether ``path`` is a regular file (``False`` for anything unreadable)."""
    return os.path.isfile(path)  # noqa: PTH113 - never raises, unlike Path.is_file


def is_dir(path: Path) -> bool:
    """Whether ``path`` is a directory (``False`` for anything unreadable)."""
    return os.path.isdir(path)  # noqa: PTH112 - never raises, unlike Path.is_dir


def expand(path: str) -> Path | None:
    """``path`` with ``~`` expanded, or ``None`` for an unknown user."""
    try:
        return Path(path).expanduser()
    except RuntimeError:
        return None


def resolved(path: Path) -> Path | None:
    """``path.resolve()``, or ``None`` where it cannot be resolved."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def utf8(name: str) -> bool:
    r"""Whether ``name`` survives JSON and a URL (it encodes as UTF-8).

    Linux filesystems accept arbitrary bytes in a name; Python hands the
    undecodable ones back as lone surrogates (``'caf\udce9.png'``), which cannot
    be encoded -- one such name would make a whole listing fail to serialise.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
