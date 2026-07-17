"""Directory browsing + folder creation for the path-picker widget.

Kept out of the HTTP layer (:mod:`typantic.web.api`) so the routes stay thin:
these functions do the actual filesystem work and return plain data, raising
:class:`FileSystemError` (mapped to HTTP 400 by the API) for a bad request. The
image gallery is the sibling precedent -- output-image scanning lives in
:mod:`typantic.web.gallery`, not inline in the route.
"""

import os
from pathlib import Path


class FileSystemError(Exception):
    """A path-picker request that cannot be served (bad name / missing parent)."""


_BROWSE_ENTRY_CAP = 50000
"""Payload ceiling for one directory listing (the picker virtualises the list)."""

# Reserved / traversal-prone names, plus separators, that must never be a single
# new-folder component. Rejecting these keeps ``parent / name`` inside ``parent``.
_INVALID_DIR_NAMES = frozenset({"", ".", ".."})
_INVALID_DIR_CHARS = frozenset({"/", "\\", "\x00"})


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _expand(path: str) -> Path | None:
    """Expand ``~`` in a user-supplied path, or ``None`` if it cannot be resolved.

    ``Path.expanduser`` raises ``RuntimeError`` for an unknown user (``~nobody``),
    which is a bad path from the picker, not a server fault.
    """
    try:
        return Path(path).expanduser()
    except RuntimeError:
        return None


def browse_directory(path: str | None) -> dict[str, object]:
    """List a directory for the path picker (falls back to home on a bad path)."""
    raw = (_expand(path) or Path.home()) if path else Path.home()
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


def make_directory(path: str, name: str) -> dict[str, object]:
    """Create one folder ``name`` under ``path`` and return its (empty) listing.

    Raises:
        FileSystemError: The name is invalid, the parent is missing, or the
            underlying ``mkdir`` failed -- each a bad request (HTTP 400).
    """
    clean = name.strip()
    if clean in _INVALID_DIR_NAMES or _INVALID_DIR_CHARS & set(clean):
        msg = "Invalid folder name."
        raise FileSystemError(msg)
    parent = _expand(path)
    if parent is None or not _is_dir(parent):
        msg = "Parent folder does not exist."
        raise FileSystemError(msg)
    target = parent / clean
    try:
        target.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise FileSystemError(str(exc)) from exc
    return browse_directory(str(target))
