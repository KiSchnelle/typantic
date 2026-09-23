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
from typing import NamedTuple
from urllib.parse import quote

from typantic.web._paths import expand, is_dir, is_file, resolved, utf8
from typantic.web.models import JobImage, JobImages, JobRecord

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_IMAGE_LIMIT = 300  # most images returned to the UI
_IMAGE_SCAN_CAP = 20000  # most directory entries visited before giving up
_IMAGE_MAX_DEPTH = 8  # deepest sub-folder walked

_THUMB_CACHE = Path.home() / ".cache" / "typantic" / "thumbnails"
# Part of every thumbnail's cache key: bump it when the rendering changes, so an
# existing cache never serves thumbnails an older renderer produced.
_THUMB_VERSION = "2"
THUMB_MIN_WIDTH = 16
THUMB_MAX_WIDTH = 1024


def artifact_roots(record: JobRecord) -> list[Path]:
    """Folders to scan for a job's output images.

    The job folder (where the command runs, so relative output lands here), plus
    an explicit absolute ``output_folder`` from the submitted config if the
    command set one elsewhere.
    """
    job_dir = resolved(Path(record.job_dir))
    roots = [job_dir] if job_dir is not None else []
    try:
        config = json.loads(Path(record.config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers JSON and decoding errors
        return roots
    output = config.get("output_folder") if isinstance(config, dict) else None
    if isinstance(output, str):
        # A config's output_folder is user input: a typo ("~results") or a NUL
        # must cost only that folder, not the job's whole gallery.
        candidate = expand(output)
        extra = resolved(candidate) if candidate and candidate.is_absolute() else None
        # Skip it if it is the job dir or nested inside it (already scanned).
        if extra is not None and not any(extra.is_relative_to(r) for r in roots):
            roots.append(extra)
    return roots


class ImageScan(NamedTuple):
    """The images under one root, newest first, and whether a cap cut it short."""

    images: list[tuple[int, Path]]  # (mtime_ns, path)
    capped: bool


def scan_images(root: Path) -> ImageScan:
    """Image files under ``root``, newest first, with a bounded, cycle-safe walk.

    Uses an explicit stack (not ``rglob``) so it can cap depth and total entries
    and skip symlinked directories — a symlink cycle or a huge output tree can
    never stall or blow up the scan.
    """
    candidates: list[tuple[int, Path]] = []
    scanned = 0
    capped = False
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
                capped = True
                stack.clear()
                break
            if not utf8(entry.name):
                continue  # its path cannot be put in a URL
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < _IMAGE_MAX_DEPTH:
                        stack.append((Path(entry.path), depth + 1))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.lower() in _IMAGE_EXTS
                ):
                    candidates.append((entry.stat().st_mtime_ns, Path(entry.path)))
            except OSError:
                continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    return ImageScan(candidates, capped)


def list_images(record: JobRecord, job_id: str) -> JobImages:
    """A job's output images across its artifact roots: once each, newest first.

    Every root is scanned before anything is cut, so a job folder full of
    images cannot crowd out a newer ``output_folder``; an image reached from two
    roots (an ``output_folder`` that contains the job folder) is listed once,
    from the first. Each URL carries the file's mtime, so a rewritten image is
    fetched afresh rather than served from the browser's cache.
    """
    found: list[tuple[int, int, Path, Path]] = []  # (mtime_ns, root index, root, path)
    seen: set[Path] = set()
    truncated = False
    for index, root in enumerate(artifact_roots(record)):
        if not is_dir(root):
            continue
        scan = scan_images(root)
        truncated = truncated or scan.capped
        for mtime_ns, path in scan.images:
            if path not in seen:
                seen.add(path)
                found.append((mtime_ns, index, root, path))
    found.sort(key=lambda item: item[0], reverse=True)  # stable: roots keep order
    if len(found) > _IMAGE_LIMIT:
        truncated = True
    images = [
        JobImage(
            name=(rel := path.relative_to(root).as_posix()),
            root=index,
            url=f"/api/jobs/{job_id}/image?root={index}&path={quote(rel)}&v={mtime_ns}",
        )
        for mtime_ns, index, root, path in found[:_IMAGE_LIMIT]
    ]
    return JobImages(images=images, truncated=truncated)


def resolve_artifact(record: JobRecord, root: int, path: str) -> Path | None:
    """Resolve an image path within an allowed root, or ``None`` if it escapes it."""
    roots = artifact_roots(record)
    if not 0 <= root < len(roots):
        return None
    base = roots[root]
    target = resolved(base / path)
    if target is None or not target.is_relative_to(base):
        return None  # unresolvable, or a path traversal attempt
    if not is_file(target) or target.suffix.lower() not in _IMAGE_EXTS:
        return None
    return target


def thumbnail(source: Path, width: int) -> Path | None:
    """Return a cached, downscaled WebP copy of ``source`` (longest edge ``width``).

    Cached by the source's path + mtime + width, so each thumbnail is built once
    and reused. Returns ``None`` if Pillow can't render the image or the cache
    can't be written, so the caller falls back to the full-resolution original.
    """
    # Deferred so importing typantic.web doesn't pull in Pillow until needed.
    from PIL import Image, ImageOps  # noqa: PLC0415

    try:
        stat = source.stat()
    except OSError:
        return None
    # The size catches a rewrite a coarse (1 s) mtime cannot see; the version
    # retires every cached thumbnail when the rendering below changes.
    fingerprint = f"{_THUMB_VERSION}|{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}"
    key = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
    cached = _THUMB_CACHE / f"{key}.webp"
    if is_file(cached):
        return cached
    try:
        _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_THUMB_CACHE, suffix=".webp")
        os.close(fd)
        try:
            with Image.open(source) as img:
                img.draft("RGB", (width, width))
                # A browser shows the full-size image already rotated by its EXIF
                # tag and composited over the page. Match both, or the thumbnail
                # disagrees with the image it links to: convert("RGB") alone drops
                # alpha and keeps the colour beneath it, turning a transparent PNG
                # (black under a clear background) into a solid black tile.
                upright = ImageOps.exif_transpose(img) or img
                if upright.mode.startswith("I;16"):
                    # A browser scales 16-bit samples down to 8 bits for display;
                    # convert("RGB") clips them, so a detector image or a 12-bit
                    # capture came out almost pure white.
                    upright = upright.point(lambda value: value / 256)
                if upright.mode in ("RGBA", "LA", "PA") or "transparency" in (
                    upright.info
                ):
                    canvas = Image.new("RGBA", upright.size, (255, 255, 255, 255))
                    rgb = Image.alpha_composite(
                        canvas,
                        upright.convert("RGBA"),
                    ).convert("RGB")
                else:
                    rgb = upright.convert("RGB")
                rgb.thumbnail((width, width), Image.Resampling.LANCZOS)
                rgb.save(tmp, "WEBP", quality=80, method=4)
            Path(tmp).replace(cached)
        finally:
            Path(tmp).unlink(missing_ok=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    return cached
