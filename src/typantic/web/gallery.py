"""Output-image gallery: find and thumbnail images a job produced.

Generic by design — any image files a job writes into its folder (both the
process and scheduler backends run with the job folder as the working
directory, so a command's default relative output lands there) are shown as a
thumbnail grid. An absolute ``output_folder`` in the submitted config, if the
command has one, is scanned too. Thumbnails are generated with Pillow and cached
on disk; the walk is bounded and cycle-safe.

The thumbnail cache lives in ``$TYPANTIC_WEB_CACHE_DIR/thumbnails``, else in
``$XDG_CACHE_HOME/typantic/thumbnails`` (``~/.cache`` when that is unset), and
the server prunes thumbnails nobody has asked for in 30 days when it starts.
"""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from typantic.web._paths import expand, is_dir, is_file, resolved, utf8
from typantic.web.models import JobImage, JobImages, JobRecord

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_IMAGE_LIMIT = 300  # most images returned to the UI
_IMAGE_SCAN_CAP = 20000  # most directory entries visited before giving up
_IMAGE_MAX_DEPTH = 8  # deepest sub-folder walked

_ENV_CACHE_DIR = "TYPANTIC_WEB_CACHE_DIR"
_THUMB_MAX_AGE_DAYS = 30  # a thumbnail unused this long is pruned at server start
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

    Walks breadth first with an explicit queue (not ``rglob``), so it can cap
    depth and total entries and skip symlinked directories: a symlink cycle or a
    huge output tree can never stall or blow up the scan. Each folder is read
    lazily, so the cap stops the reading too, and breadth first means a deep
    subtree cannot use up the budget before a shallow sibling folder is seen.
    """
    images: list[tuple[int, Path]] = []
    budget = _IMAGE_SCAN_CAP
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue:
        directory, depth = queue.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if budget == 0:
                        return ImageScan(_newest_first(images), capped=True)
                    budget -= 1
                    _visit(entry, depth, images, queue)
        except OSError:
            continue
    return ImageScan(_newest_first(images), capped=False)


def _visit(
    entry: os.DirEntry[str],
    depth: int,
    images: list[tuple[int, Path]],
    queue: deque[tuple[Path, int]],
) -> None:
    """Collect ``entry`` if it is an image, or queue it if it is a folder."""
    if not utf8(entry.name):
        return  # its path cannot be put in a URL
    with contextlib.suppress(OSError):
        if entry.is_dir(follow_symlinks=False):
            if depth < _IMAGE_MAX_DEPTH:
                queue.append((Path(entry.path), depth + 1))
        elif (
            entry.is_file(follow_symlinks=False)
            and Path(entry.name).suffix.lower() in _IMAGE_EXTS
        ):
            images.append((entry.stat().st_mtime_ns, Path(entry.path)))


def _newest_first(images: list[tuple[int, Path]]) -> list[tuple[int, Path]]:
    return sorted(images, key=lambda item: item[0], reverse=True)


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


def thumbnail(source: Path, width: int) -> bytes | None:
    """A downscaled WebP of ``source`` (longest edge ``width``), cached on disk.

    Cached by the source's path, mtime, size and width, so each thumbnail is
    rendered once; a cache that cannot be written costs a re-render next time,
    not the thumbnail. ``None`` means the image cannot be thumbnailed (Pillow
    cannot read it, or it is too large to decode). The caller must not fall back
    to the original, which may be hundreds of megabytes.
    """
    try:
        stat = source.stat()
    except OSError:
        return None
    # The size catches a rewrite a coarse (1 s) mtime cannot see; the version
    # retires every cached thumbnail when the rendering below changes.
    fingerprint = f"{_THUMB_VERSION}|{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}"
    key = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
    cached = _cache_dir() / f"{key}.webp"
    data = _read_cached(cached)
    if data is None:
        data = _render(source, width)
        if data is not None:
            _write_cached(cached, data)
    return data


def _cache_dir() -> Path:
    """Where thumbnails are cached, from the environment at the time of asking."""
    override = os.environ.get(_ENV_CACHE_DIR)
    if override:
        return (expand(override) or Path(override)) / "thumbnails"
    # The XDG spec: an unset, empty or relative XDG_CACHE_HOME means ~/.cache.
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"  # noqa: PTH117
    return base / "typantic" / "thumbnails"


def _read_cached(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    with contextlib.suppress(OSError):
        os.utime(path)  # in use: keeps it out of the next prune
    return data


def _write_cached(path: Path, data: bytes) -> None:
    """Store a rendered thumbnail atomically, or skip it if the cache is unwritable."""
    try:
        # Thumbnails show a job's outputs, so the cache is as private as the
        # job store: the folder is 0700, and mkstemp creates each file 0600.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        Path(tmp).replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()


def _render(source: Path, width: int) -> bytes | None:
    """``source`` as a WebP at most ``width`` px on its longest edge, or ``None``.

    The image is shrunk right after decoding, so rotating and compositing work
    on the small copy rather than on the full-size image.
    """
    # Deferred so importing typantic.web doesn't pull in Pillow until needed.
    from PIL import Image, ImageOps  # noqa: PLC0415

    try:
        with Image.open(source) as img:
            # A JPEG decodes at a fraction of its size; ask for twice the
            # thumbnail, which is what Image.thumbnail() itself would ask for.
            img.draft("RGB", (2 * width, 2 * width))
            limit = Image.MAX_IMAGE_PIXELS
            if limit is not None and img.width * img.height > limit:
                # Pillow refuses only twice its limit, and decodes anything
                # below that in full: 150 MP is a 600 MB decode for one tile.
                return None
            small: Image.Image = img
            if img.mode.startswith("I;16"):
                # A browser scales 16-bit samples down to 8 bits for display;
                # convert("L") clips them, so a detector image or a 12-bit
                # capture came out almost pure white. Pillow cannot shrink I;16.
                small = img.point(lambda value: value / 256).convert("L")
            elif img.mode in ("1", "P", "PA") or "transparency" in img.info:
                # Pillow shrinks palette and bilevel images nearest-neighbour,
                # and a colour key (PNG tRNS) stops matching once pixels blend.
                alpha = img.mode == "PA" or "transparency" in img.info
                small = img.convert("RGBA" if alpha else "RGB")
            small.thumbnail((width, width), Image.Resampling.LANCZOS)
            # A browser shows the full-size image already rotated by its EXIF
            # tag and composited over the page. Match both, or the thumbnail
            # disagrees with the image it links to: convert("RGB") alone drops
            # alpha and keeps the colour beneath it, turning a transparent PNG
            # (black under a clear background) into a solid black tile.
            ImageOps.exif_transpose(small, in_place=True)
            if small.mode in ("RGBA", "LA"):
                white = Image.new("RGBA", small.size, (255, 255, 255, 255))
                small = Image.alpha_composite(white, small.convert("RGBA"))
            out = io.BytesIO()
            small.convert("RGB").save(out, "WEBP", quality=80, method=4)
    except (KeyError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        # KeyError: this Pillow was built without a WebP encoder.
        return None
    return out.getvalue()


def prune_thumbnails(max_age_days: float = _THUMB_MAX_AGE_DAYS) -> int:
    """Delete cached thumbnails nobody has asked for in ``max_age_days``.

    Serving a thumbnail from the cache refreshes its mtime, so what is pruned is
    what nobody looked at. Anything that cannot be read or removed is left.

    Returns:
        How many files were removed.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    with contextlib.suppress(OSError), os.scandir(_cache_dir()) as entries:
        for entry in entries:
            try:
                stale = (
                    entry.is_file(follow_symlinks=False)
                    and entry.stat(follow_symlinks=False).st_mtime < cutoff
                )
                if stale:
                    os.unlink(entry.path)  # noqa: PTH108 - a DirEntry's path
                    removed += 1
            except OSError:
                continue
    return removed
