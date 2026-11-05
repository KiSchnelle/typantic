"""What deleting a job does to its output folder, decided before anything goes.

A job's ``output_folder`` is a path somebody typed into a form, and deleting it is
recursive and final. Whoever deletes the job sees the path and confirms it, so what
this module adds is the folders nobody should be able to confirm away: home, the
job store, a folder another job uses, a path that may not be this job's at all.

Folders are compared by identity (device and inode), never by spelling, as one
folder can have many: a case-insensitive filesystem, a firmlink.
"""

import os
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from typantic.web._files import read_json_mapping
from typantic.web._paths import expand, is_dir, resolved
from typantic.web.models import JobRecord, JobRemoval

# A folder directly under the filesystem root (``/scratch``) is a mount or a
# system directory, never one job's output.
_MIN_PARTS = 3

# On macOS, /System/Volumes/Data holds every user's files, reached by firmlink: by
# inode it is no ancestor of home, and it does not even report as a mount point.
_MACOS_SYSTEM = Path("/System")

_Inode = tuple[int, int]


class FolderId(NamedTuple):
    """A folder's identity and that of everything above it, as far as they exist."""

    own: _Inode | None
    chain: frozenset[_Inode]


def _inode(path: Path) -> _Inode | None:
    try:
        info = path.stat()
    except (OSError, ValueError):
        return None
    return info.st_dev, info.st_ino


def folder_id(path: Path) -> FolderId:
    """Identify ``path``, which need not exist yet.

    Resolved first: the folders above a symlinked path are those of its target, so
    a symlinked ``/home`` or job store still counts as inside what really holds it.
    """
    real = resolved(path) or path
    chain = {
        inode for part in (real, *real.parents) if (inode := _inode(part)) is not None
    }
    return FolderId(_inode(real), frozenset(chain))


def _holds(outer: FolderId, inner: FolderId) -> bool:
    """Whether ``inner`` is ``outer`` or lies inside it."""
    return outer.own is not None and outer.own in inner.chain


def overlapping(first: FolderId, second: FolderId) -> bool:
    """Whether removing either folder would touch the other."""
    return _holds(first, second) or _holds(second, first)


def _configured_value(record: JobRecord) -> str | None:
    """The ``output_folder`` string in a job's submitted config, if it has one."""
    config = read_json_mapping(Path(record.config_path))
    output = config.get("output_folder") if config is not None else None
    return output if isinstance(output, str) else None


def _absolute(value: str | None) -> Path | None:
    candidate = expand(value) if value is not None else None
    return candidate if candidate is not None and candidate.is_absolute() else None


def configured_output(record: JobRecord) -> Path | None:
    """The absolute ``output_folder`` in a job's submitted config, as spelled.

    ``None`` when there is none, or it is relative (which lands in the job folder,
    where the command runs). A config's value is user input, so a typo or a
    missing config costs only this answer.
    """
    return _absolute(_configured_value(record))


def plan_removal(  # noqa: PLR0911 - each outcome is its own short path
    record: JobRecord,
    *,
    job_dir: Path,
    jobs_root: Path,
    local_paths: bool,
    others: Mapping[str, FolderId],
) -> JobRemoval:
    """Decide whether deleting ``record`` may also remove its output folder.

    Args:
        record: The job about to go.
        job_dir: Its folder in the store, which goes with it regardless.
        jobs_root: The store's root, which holds every job's folder.
        local_paths: Whether the job's paths name this machine's files, which
            holds only for backends that say so.
        others: The output folders of jobs that stay, by job id.
    """
    value = _configured_value(record)
    candidate = _absolute(value)
    if value is None or candidate is None:
        return JobRemoval(job_id=record.id)
    target = resolved(candidate)
    if target is None:
        return _kept(record, candidate, "its path cannot be resolved")
    target_id = folder_id(target)
    if _holds(folder_id(job_dir), target_id):
        return JobRemoval(job_id=record.id)  # goes with the job's folder
    reason = _spelling_reason(value, candidate, record, local_paths=local_paths)
    if reason is not None:
        return _kept(record, candidate, reason)
    if not is_dir(target):
        return JobRemoval(job_id=record.id)  # nothing there to remove
    reason = _location_reason(candidate, target, target_id, jobs_root, others)
    if reason is not None:
        return _kept(record, candidate, reason)
    return JobRemoval(job_id=record.id, output_folder=str(target), removes_output=True)


def _kept(record: JobRecord, candidate: Path, reason: str) -> JobRemoval:
    return JobRemoval(
        job_id=record.id, output_folder=str(candidate), kept_reason=reason
    )


def _spelling_reason(
    value: str,
    candidate: Path,
    record: JobRecord,
    *,
    local_paths: bool,
) -> str | None:
    """Why the path may not name the folder the job wrote to."""
    if value.startswith("~"):
        return "it starts with ~, which the job may not have expanded"
    if ".." in candidate.parts:
        return "its path contains .."
    if not local_paths:
        return f"the {record.backend} backend's paths may not be on this machine"
    if record.host is not None and record.host != socket.gethostname():
        return f"the job ran on {record.host}, not on this machine"
    return None


def _location_reason(  # noqa: C901, PLR0911 - one return per folder that must stay
    candidate: Path,
    target: Path,
    target_id: FolderId,
    jobs_root: Path,
    others: Mapping[str, FolderId],
) -> str | None:
    """Why this folder must stay, whoever confirms it."""
    if os.path.islink(candidate):  # noqa: PTH114 - never raises, unlike Path.is_symlink
        return "it is a symbolic link"
    root = folder_id(jobs_root)
    if _holds(root, target_id):
        return "it is inside the job store"
    if _holds(target_id, root):
        return "it contains the job store"
    home = expand("~")
    home_id = folder_id(home) if home is not None else None
    if home_id is None or home_id.own is None:
        return "your home folder cannot be found, so it may contain it"
    if _holds(target_id, home_id):
        return "it contains your home folder"
    if len(target.parts) < _MIN_PARTS:
        return "it is a top-level folder"
    if os.path.ismount(target):
        return "it is a mount point"
    if _holds(folder_id(_MACOS_SYSTEM), target_id):
        return "it is a macOS system folder"
    for job_id, other in others.items():
        if overlapping(target_id, other):
            return f"it overlaps the output folder of job {job_id}"
    return None
