"""Output-image gallery: find and thumbnail images a job produced.

Generic by design — any image files a job writes into its folder (both the
process and scheduler backends run with the job folder as the working
directory, so a command's default relative output lands there) are shown as a
thumbnail grid. An absolute ``output_folder`` in the submitted config, if the
command has one, is scanned too. Thumbnails are generated with Pillow and cached
on disk; the walk is bounded and cycle-safe.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from typantic.web.models import JobRecord

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_IMAGE_LIMIT = 300  # most images returned to the UI
_IMAGE_SCAN_CAP = 20000  # most directory entries visited before giving up
_IMAGE_MAX_DEPTH = 8  # deepest sub-folder walked

_THUMB_CACHE = Path.home() / ".cache" / "typantic" / "thumbnails"
THUMB_MIN_WIDTH = 16
THUMB_MAX_WIDTH = 1024


def artifact_roots(record: JobRecord) -> list[Path]:
    """Folders to scan for a job's output images.

    The job folder (where the command runs, so relative output lands here), plus
    an explicit absolute ``output_folder`` from the submitted config if the
    command set one elsewhere.
    """
    roots = [Path(record.job_dir).resolve()]
    try:
        config = json.loads(Path(record.config_path).read_text())
    except (OSError, json.JSONDecodeError):
        return roots
    output = config.get("output_folder") if isinstance(config, dict) else None
    if isinstance(output, str):
        candidate = Path(output).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
            # Skip if it is the job dir or nested inside it (already scanned).
            if not any(resolved.is_relative_to(root) for root in roots):
                roots.append(resolved)
    return roots


def scan_images(root: Path) -> list[Path]:
    """Image files under ``root``, newest first, with a bounded, cycle-safe walk.

    Uses an explicit stack (not ``rglob``) so it can cap depth and total entries
    and skip symlinked directories — a symlink cycle or a huge output tree can
    never stall or blow up the scan. Sorting by mtime keeps the most recent
    images under the ``_IMAGE_LIMIT`` cap.
    """
    candidates: list[tuple[float, Path]] = []
    scanned = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            scanned += 1
            if scanned > _IMAGE_SCAN_CAP:
                stack.clear()
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < _IMAGE_MAX_DEPTH:
                        stack.append((Path(entry.path), depth + 1))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.lower() in _IMAGE_EXTS
                ):
                    candidates.append((entry.stat().st_mtime, Path(entry.path)))
            except OSError:
                continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates]


def list_images(record: JobRecord, job_id: str) -> list[dict[str, object]]:
    """Find output images across a job's artifact roots (bounded), newest first."""
    found: list[dict[str, object]] = []
    for index, root in enumerate(artifact_roots(record)):
        if not root.is_dir():
            continue
        for file in scan_images(root):
            if len(found) >= _IMAGE_LIMIT:
                return found
            rel = file.relative_to(root).as_posix()
            found.append(
                {
                    "name": rel,
                    "root": index,
                    "url": f"/api/jobs/{job_id}/image?root={index}&path={quote(rel)}",
                },
            )
    return found


def resolve_artifact(record: JobRecord, root: int, path: str) -> Path | None:
    """Resolve an image path within an allowed root, or ``None`` if it escapes it."""
    roots = artifact_roots(record)
    if not 0 <= root < len(roots):
        return None
    base = roots[root].resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base):
        return None  # path traversal attempt
    if not target.is_file() or target.suffix.lower() not in _IMAGE_EXTS:
        return None
    return target


def thumbnail(source: Path, width: int) -> Path | None:
    """Return a cached, downscaled WebP copy of ``source`` (longest edge ``width``).

    Cached by the source's path + mtime + width, so each thumbnail is built once
    and reused. Returns ``None`` if Pillow can't render the image or the cache
    can't be written, so the caller falls back to the full-resolution original.
    """
    # Deferred so importing typantic.web doesn't pull in Pillow until needed.
    from PIL import Image  # noqa: PLC0415

    try:
        mtime = source.stat().st_mtime_ns
    except OSError:
        return None
    key = hashlib.sha256(f"{source}|{mtime}|{width}".encode()).hexdigest()[:32]
    cached = _THUMB_CACHE / f"{key}.webp"
    if cached.is_file():
        return cached
    try:
        _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_THUMB_CACHE, suffix=".webp")
        os.close(fd)
        try:
            with Image.open(source) as img:
                img.draft("RGB", (width, width))
                rgb = img.convert("RGB")
                rgb.thumbnail((width, width), Image.Resampling.LANCZOS)
                rgb.save(tmp, "WEBP", quality=80, method=4)
            Path(tmp).replace(cached)
        finally:
            Path(tmp).unlink(missing_ok=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    return cached
