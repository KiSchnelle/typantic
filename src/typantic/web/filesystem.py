"""Directory browsing + folder creation for the path-picker widget.

Kept out of the HTTP layer (:mod:`typantic.web.api`) so the routes stay thin:
these functions do the actual filesystem work and return plain data, raising
:class:`FileSystemError` (mapped to HTTP 400 by the API) for a bad request. The
image gallery is the sibling precedent -- output-image scanning lives in
:mod:`typantic.web.gallery`, not inline in the route.
"""

import os
from pathlib import Path

from typantic.web._paths import expand, is_dir, is_file, utf8
from typantic.web.models import FsEntry, FsListing


class FileSystemError(Exception):
    """A path-picker request that cannot be served (bad name / missing parent)."""


_BROWSE_ENTRY_CAP = 50000
"""Payload ceiling for one directory listing (the picker virtualises the list)."""

# Reserved / traversal-prone names, plus separators, that must never be a single
# new-folder component. Rejecting these keeps ``parent / name`` inside ``parent``.
_INVALID_DIR_NAMES = frozenset({"", ".", ".."})
_INVALID_DIR_CHARS = frozenset({"/", "\\", "\x00"})


def _absolute(path: Path) -> Path:
    """``path`` made absolute from the user's home, lexically -- not resolved.

    A relative path is taken from home rather than the server's working
    directory (which a user never sees), and symlinks are kept as spelled: the
    path a user picks is written into the job's config, and the resolved form
    (``/scratch`` turned into ``/lustre/...``) may not exist where the job runs.
    """
    return Path(os.path.abspath(Path.home() / path))  # noqa: PTH100 - lexical, by design


def browse_directory(path: str | None) -> FsListing:
    """List a directory for the path picker (falls back to home on a bad path)."""
    expanded = expand(path) if path else None
    raw = _absolute(expanded) if expanded is not None else Path.home()
    if is_file(raw):
        raw = raw.parent
    if not is_dir(raw):
        raw = Path.home()
    base = raw

    listed: list[tuple[bool, str]] = []
    error: str | None = None
    try:
        with os.scandir(base) as scan:
            for entry in scan:
                if not utf8(entry.name):
                    continue  # cannot be listed, picked, or put in a URL
                try:
                    is_dir_entry = entry.is_dir()
                except OSError:
                    is_dir_entry = False
                listed.append((is_dir_entry, entry.name))
    except OSError as exc:
        error = str(exc)

    listed.sort(key=lambda item: (not item[0], item[1].lower()))
    entries = [
        FsEntry(name=name, is_dir=is_dir) for is_dir, name in listed[:_BROWSE_ENTRY_CAP]
    ]
    parent = str(base.parent) if base.parent != base else None
    return FsListing(
        path=str(base),
        parent=parent,
        entries=entries,
        error=error,
        total=len(listed),
        truncated=len(listed) > _BROWSE_ENTRY_CAP,
    )


def make_directory(path: str, name: str) -> FsListing:
    """Create one folder ``name`` under ``path`` and return its (empty) listing.

    Raises:
        FileSystemError: The name is invalid, the parent is missing, or the
            underlying ``mkdir`` failed -- each a bad request (HTTP 400).
    """
    clean = name.strip()
    if (
        clean in _INVALID_DIR_NAMES
        or _INVALID_DIR_CHARS & set(clean)
        or not clean.isprintable()
        or not utf8(clean)
    ):
        # A control character or a lone surrogate would make a folder no listing
        # could show again.
        msg = "Invalid folder name."
        raise FileSystemError(msg)
    expanded = expand(path)
    parent = _absolute(expanded) if expanded is not None else None
    if parent is None or not is_dir(parent):
        msg = "Parent folder does not exist."
        raise FileSystemError(msg)
    target = parent / clean
    try:
        target.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise FileSystemError(str(exc)) from exc
    return browse_directory(str(target))
