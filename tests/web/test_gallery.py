import io
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from typantic.web import gallery
from typantic.web.models import JobRecord


def _record(job_dir, config=None):
    cfg = job_dir / "submit_config.json"
    cfg.write_text(json.dumps(config if config is not None else {}))
    return JobRecord(
        id="j",
        command_key="a/b",
        app="a",
        command="b",
        title="T",
        backend="local",
        job_dir=str(job_dir),
        config_path=str(cfg),
        log_path=str(job_dir / "job.log"),
        created_at=datetime.now(UTC),
    )


class _Listing:
    """A stand-in for ``os.scandir``: usable in a with-block, read lazily."""

    def __init__(self, entries):
        self._entries = iter(entries)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._entries)


def _open(data):
    """A rendered thumbnail (bytes) opened as an image."""
    return Image.open(io.BytesIO(data))


def _png(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(path)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# --- artifact_roots ---


def test_artifact_roots_job_dir_only(tmp_path):
    assert gallery.artifact_roots(_record(tmp_path)) == [tmp_path.resolve()]


def test_artifact_roots_adds_absolute_output_folder(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    out = tmp_path / "out"  # sibling of the job dir, not nested inside it
    out.mkdir()
    roots = gallery.artifact_roots(_record(job, {"output_folder": str(out)}))
    assert roots == [job.resolve(), out.resolve()]


def test_artifact_roots_skips_nested_output_folder(tmp_path):
    nested = tmp_path / "inside"
    roots = gallery.artifact_roots(_record(tmp_path, {"output_folder": str(nested)}))
    assert roots == [tmp_path.resolve()]


def test_artifact_roots_ignores_relative_output_folder(tmp_path):
    roots = gallery.artifact_roots(_record(tmp_path, {"output_folder": "rel/dir"}))
    assert roots == [tmp_path.resolve()]


def test_artifact_roots_missing_config(tmp_path):
    record = _record(tmp_path)
    Path(record.config_path).unlink()
    assert gallery.artifact_roots(record) == [tmp_path.resolve()]


def test_artifact_roots_non_dict_config(tmp_path):
    record = _record(tmp_path)
    Path(record.config_path).write_text("[1, 2]")
    assert gallery.artifact_roots(record) == [tmp_path.resolve()]


# --- scan_images ---


def test_scan_images_newest_first_recursive(tmp_path):
    _png(tmp_path / "old.png", mtime=100)
    _png(tmp_path / "sub" / "new.png", mtime=200)
    (tmp_path / "notes.txt").write_text("x")
    scan = gallery.scan_images(tmp_path)
    assert [path.name for _, path in scan.images] == ["new.png", "old.png"]
    assert not scan.capped


def test_scan_images_missing_root(tmp_path):
    assert gallery.scan_images(tmp_path / "nope").images == []


def test_scan_images_depth_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_MAX_DEPTH", 0)
    _png(tmp_path / "top.png")
    _png(tmp_path / "deep" / "img.png")
    scan = gallery.scan_images(tmp_path)
    assert [path.name for _, path in scan.images] == ["top.png"]


def test_scan_images_scan_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_SCAN_CAP", 1)
    _png(tmp_path / "a.png")
    _png(tmp_path / "b.png")
    _png(tmp_path / "c.png")
    scan = gallery.scan_images(tmp_path)
    assert len(scan.images) <= 1
    assert scan.capped


def test_scan_images_entry_error_is_skipped(tmp_path, monkeypatch):
    class BadEntry:
        name = "x.png"
        path = str(tmp_path / "x.png")

        def is_dir(self, *, follow_symlinks=True):
            raise OSError

    monkeypatch.setattr(gallery.os, "scandir", lambda _p: _Listing([BadEntry()]))
    assert gallery.scan_images(tmp_path).images == []


# --- list_images ---


def test_list_images(tmp_path):
    _png(tmp_path / "a.png", mtime=100)
    record = _record(tmp_path)
    images = gallery.list_images(record, "job1").images
    assert images[0].name == "a.png"
    assert images[0].root == 0
    assert "job1/image?root=0&path=a.png" in images[0].url


def test_list_images_skips_non_dir_root(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    ghost = tmp_path / "ghost"  # external, absolute, never created -> a non-dir root
    _png(job / "a.png")
    record = _record(job, {"output_folder": str(ghost)})
    images = gallery.list_images(record, "job1").images
    assert [img.root for img in images] == [0]


def test_list_images_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_LIMIT", 1)
    _png(tmp_path / "a.png", mtime=100)
    _png(tmp_path / "b.png", mtime=200)
    listing = gallery.list_images(_record(tmp_path), "j")
    assert len(listing.images) == 1
    assert listing.truncated


# --- resolve_artifact ---


def test_resolve_artifact_valid(tmp_path):
    _png(tmp_path / "a.png")
    resolved = gallery.resolve_artifact(_record(tmp_path), 0, "a.png")
    assert resolved == (tmp_path / "a.png").resolve()


def test_resolve_artifact_root_out_of_range(tmp_path):
    assert gallery.resolve_artifact(_record(tmp_path), 5, "a.png") is None


def test_resolve_artifact_traversal(tmp_path):
    assert gallery.resolve_artifact(_record(tmp_path), 0, "../secret.png") is None


def test_resolve_artifact_non_image(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert gallery.resolve_artifact(_record(tmp_path), 0, "notes.txt") is None


def test_resolve_artifact_missing_file(tmp_path):
    assert gallery.resolve_artifact(_record(tmp_path), 0, "ghost.png") is None


# --- thumbnail ---


def test_thumbnail_creates_and_caches(tmp_path, monkeypatch):
    source = tmp_path / "a.png"
    _png(source)
    first = gallery.thumbnail(source, 32)
    assert first is not None
    assert _open(first).format == "WEBP"
    assert [p.suffix for p in gallery._cache_dir().iterdir()] == [".webp"]

    def no_render(*_args):
        raise AssertionError("rendered again despite the cache")

    monkeypatch.setattr(gallery, "_render", no_render)
    assert gallery.thumbnail(source, 32) == first  # served from the cache


def test_thumbnail_corrupt_image(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    assert gallery.thumbnail(bad, 32) is None


def test_thumbnail_missing_source(tmp_path):
    assert gallery.thumbnail(tmp_path / "ghost.png", 32) is None


def test_transparent_png_thumbnails_onto_white_not_black(tmp_path):
    # convert("RGB") alone drops alpha and keeps the colour beneath it, so a
    # transparent PNG (black under a clear background) came out a black tile --
    # disagreeing with the full-size view the browser composites over the page.
    src = tmp_path / "plot.png"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    img.putpixel((32, 32), (255, 0, 0, 255))
    img.save(src)

    thumb = gallery.thumbnail(src, 32)
    assert thumb is not None
    assert _open(thumb).convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_thumbnail_applies_exif_orientation(tmp_path):
    # The browser rotates the full-size image by its EXIF tag; the thumbnail must
    # match or the grid shows it sideways.
    src = tmp_path / "photo.jpg"
    # A wide image tagged "rotate 90" is upright-portrait once transposed.
    img = Image.new("RGB", (64, 32), (10, 20, 30))
    exif = img.getexif()
    exif[274] = 6  # Orientation: rotate 90 CW
    img.save(src, exif=exif)

    thumb = gallery.thumbnail(src, 64)
    assert thumb is not None
    w, h = _open(thumb).size
    assert h > w  # transposed; without exif_transpose this would still be wide


def test_tests_never_touch_the_real_thumbnail_cache():
    real = Path.home() / ".cache" / "typantic" / "thumbnails"
    assert gallery._cache_dir() != real


# --- a bad path never takes the gallery down ---


@pytest.mark.parametrize("output", ["~nosuchuser12345/x", "/tmp/bad\x00name"])
def test_a_bad_output_folder_is_skipped_not_fatal(tmp_path, output):
    job = tmp_path / "job"
    _png(job / "a.png")
    record = _record(job, {"output_folder": output})
    assert gallery.artifact_roots(record) == [job.resolve()]
    listing = gallery.list_images(record, "j")
    assert [image.name for image in listing.images] == ["a.png"]


def test_an_undecodable_config_leaves_only_the_job_folder(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    record = _record(job)
    Path(record.config_path).write_bytes(b"\xff\xfe not json")
    assert gallery.artifact_roots(record) == [job.resolve()]


def test_a_nul_in_the_image_path_is_not_found(tmp_path):
    job = tmp_path / "job"
    _png(job / "a.png")
    assert gallery.resolve_artifact(_record(job), 0, "a\x00.png") is None


def test_the_gallery_survives_path_checks_that_raise(tmp_path, monkeypatch):
    # Python 3.12/3.13 behaviour, reproduced on any interpreter.
    job = tmp_path / "job"
    _png(job / "a.png")
    record = _record(job)

    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", refuse)
    monkeypatch.setattr(Path, "is_dir", refuse)
    listing = gallery.list_images(record, "j")
    assert [image.name for image in listing.images] == ["a.png"]
    assert gallery.resolve_artifact(record, 0, "a.png") == (job / "a.png").resolve()


def test_a_name_that_is_not_utf8_is_skipped(tmp_path, monkeypatch):
    real_scandir = os.scandir

    class Named:
        def __init__(self, entry, name):
            self._entry, self.name = entry, name
            self.path = entry.path

        def __getattr__(self, attr):
            return getattr(self._entry, attr)

    def scandir(directory):
        entries = list(real_scandir(directory))
        return _Listing(
            Named(e, "caf\udce9.png") if e.name == "b.png" else e for e in entries
        )

    job = tmp_path / "job"
    _png(job / "a.png")
    _png(job / "b.png")
    monkeypatch.setattr(gallery.os, "scandir", scandir)
    names = [image.name for image in gallery.list_images(_record(job), "j").images]
    assert names == ["a.png"]


# --- the gallery lists each image once, newest first, and says when it stops ---


def test_an_ancestor_output_folder_does_not_list_images_twice(tmp_path):
    job = tmp_path / "jobs" / "job"
    _png(job / "a.png")
    record = _record(job, {"output_folder": str(tmp_path)})
    names = [image.name for image in gallery.list_images(record, "j").images]
    assert names == ["a.png"]


def test_images_are_newest_first_across_folders(tmp_path):
    job, out = tmp_path / "job", tmp_path / "out"
    _png(job / "old.png", mtime=100)
    _png(out / "new.png", mtime=200)
    record = _record(job, {"output_folder": str(out)})
    names = [image.name for image in gallery.list_images(record, "j").images]
    assert names == ["new.png", "old.png"]


def test_a_full_job_folder_does_not_hide_the_output_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_LIMIT", 1)
    job, out = tmp_path / "job", tmp_path / "out"
    _png(job / "old.png", mtime=100)
    _png(out / "new.png", mtime=200)
    listing = gallery.list_images(_record(job, {"output_folder": str(out)}), "j")
    assert [image.name for image in listing.images] == ["new.png"]
    assert listing.truncated


def test_a_capped_scan_is_reported_as_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_SCAN_CAP", 1)
    _png(tmp_path / "a.png")
    _png(tmp_path / "b.png")
    assert gallery.list_images(_record(tmp_path), "j").truncated


def test_an_image_url_changes_when_the_file_is_rewritten(tmp_path):
    _png(tmp_path / "a.png", mtime=100)
    first = gallery.list_images(_record(tmp_path), "j").images[0].url
    _png(tmp_path / "a.png", mtime=200)
    second = gallery.list_images(_record(tmp_path), "j").images[0].url
    assert first != second
    assert "&v=" in second


# --- thumbnails ---


def test_a_16_bit_grayscale_thumbnail_is_not_washed_out(tmp_path):
    # A browser shows a 0..65535 ramp black to white; clipping it to 8 bits made
    # the thumbnail almost pure white.
    src = tmp_path / "ramp.png"
    ramp = Image.new("I;16", (256, 16))
    ramp.putdata([x * 257 for _ in range(16) for x in range(256)])
    ramp.save(src)
    thumb = gallery.thumbnail(src, 64)
    assert thumb is not None
    with _open(thumb) as img:
        pixels = img.convert("L").get_flattened_data()
        mean = sum(pixels) / len(pixels)
    assert 100 < mean < 156


def test_a_file_rewritten_with_the_same_mtime_gets_a_new_thumbnail(tmp_path):
    # Coarse-mtime filesystems can leave the mtime unchanged across a rewrite; the
    # size is part of the cache key too.
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "black").save(src)
    stamp = src.stat().st_mtime_ns
    first = gallery.thumbnail(src, 32)
    Image.new("RGB", (64, 64), "white").save(src)
    os.utime(src, ns=(stamp, stamp))
    second = gallery.thumbnail(src, 32)
    assert first != second


def test_a_new_renderer_does_not_serve_old_thumbnails(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    _png(src)
    gallery.thumbnail(src, 32)
    (cached,) = gallery._cache_dir().iterdir()
    cached.write_bytes(b"rendered by the old renderer")
    assert gallery.thumbnail(src, 32) == b"rendered by the old renderer"
    monkeypatch.setattr(gallery, "_THUMB_VERSION", "next")
    assert gallery.thumbnail(src, 32) != b"rendered by the old renderer"


def test_a_palette_image_is_shrunk_smoothly(tmp_path):
    # Pillow shrinks a palette image nearest-neighbour, which turns a fine
    # pattern into noise; the browser's view of it averages the two colours.
    src = tmp_path / "checker.png"
    img = Image.new("P", (64, 64))
    img.putpalette([255, 0, 0, 0, 0, 255])
    img.putdata([(x + y) % 2 for y in range(64) for x in range(64)])
    img.save(src)
    with Image.open(src) as check:
        assert check.mode == "P"
    thumb = gallery.thumbnail(src, 16)
    assert thumb is not None
    red, _, blue = _open(thumb).convert("RGB").getpixel((8, 8))
    assert 64 < red < 192
    assert 64 < blue < 192


def test_a_colour_keyed_png_leaves_no_fringe(tmp_path):
    # A colour key (PNG tRNS on an RGB image) stops matching once pixels are
    # blended, so it must become transparency before the image is shrunk:
    # otherwise the keyed black bleeds into its neighbours as a dark fringe.
    src = tmp_path / "keyed.png"
    img = Image.new("RGB", (64, 64), (0, 0, 0))
    img.paste((255, 0, 0), (32, 0, 64, 64))
    img.save(src, transparency=(0, 0, 0))
    thumb = gallery.thumbnail(src, 16)
    assert thumb is not None
    darkest = min(_open(thumb).convert("L").get_flattened_data())
    assert darkest > 60  # red over white is ~76 in L; black would be ~0


# --- thumbnails: bounded work, and an answer whatever the cache does ---


def test_an_image_over_the_pixel_limit_is_not_decoded(tmp_path, monkeypatch):
    # Pillow refuses only twice its limit at open and decodes anything below
    # that in full -- a 150 MP plot is a 600 MB decode for one tile.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3000)
    src = tmp_path / "huge.png"
    Image.new("RGB", (64, 64)).save(src)  # 4096 px: over the limit, under twice it
    with pytest.warns(Image.DecompressionBombWarning):
        assert gallery.thumbnail(src, 32) is None


def test_a_large_jpeg_is_measured_after_draft(tmp_path, monkeypatch):
    # A JPEG decodes at a fraction of its size, so the limit applies to that.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3000)
    src = tmp_path / "huge.jpg"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(src)
    with pytest.warns(Image.DecompressionBombWarning):
        thumb = gallery.thumbnail(src, 16)
    assert thumb is not None
    assert _open(thumb).size == (16, 16)


def test_a_missing_webp_encoder_is_no_thumbnail(tmp_path, monkeypatch):
    # A Pillow built without libwebp raises KeyError on save.
    Image.init()  # registers every plugin, so the removal below sticks
    monkeypatch.delitem(Image.SAVE, "WEBP")
    src = tmp_path / "a.png"
    _png(src)
    assert gallery.thumbnail(src, 32) is None


def test_an_unwritable_cache_still_returns_the_thumbnail(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("TYPANTIC_WEB_CACHE_DIR", str(blocker))
    src = tmp_path / "a.png"
    _png(src)
    thumb = gallery.thumbnail(src, 32)
    assert thumb is not None
    assert _open(thumb).format == "WEBP"


def test_a_failed_cache_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    _png(src)

    def refuse(*_args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", refuse)
    assert gallery.thumbnail(src, 32) is not None
    assert list(gallery._cache_dir().iterdir()) == []


def test_the_thumbnail_cache_is_private(tmp_path):
    src = tmp_path / "a.png"
    _png(src)
    gallery.thumbnail(src, 32)
    cache = gallery._cache_dir()
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700
    (cached,) = cache.iterdir()
    assert stat.S_IMODE(cached.stat().st_mode) == 0o600


# --- where the thumbnail cache lives ---


def test_the_cache_follows_typantic_web_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPANTIC_WEB_CACHE_DIR", str(tmp_path / "c"))
    src = tmp_path / "a.png"
    _png(src)
    gallery.thumbnail(src, 32)
    assert gallery._cache_dir() == tmp_path / "c" / "thumbnails"
    assert len(list((tmp_path / "c" / "thumbnails").iterdir())) == 1


def test_the_cache_dir_expands_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TYPANTIC_WEB_CACHE_DIR", "~/c")
    assert gallery._cache_dir() == tmp_path / "c" / "thumbnails"


def test_the_cache_follows_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPANTIC_WEB_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert gallery._cache_dir() == tmp_path / "xdg" / "typantic" / "thumbnails"


@pytest.mark.parametrize("xdg", [None, "", "relative/cache"])
def test_the_cache_defaults_to_dot_cache(tmp_path, monkeypatch, xdg):
    # The XDG spec: an unset, empty or relative XDG_CACHE_HOME means ~/.cache.
    monkeypatch.delenv("TYPANTIC_WEB_CACHE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    if xdg is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", xdg)
    assert gallery._cache_dir() == tmp_path / ".cache" / "typantic" / "thumbnails"


# --- pruning thumbnails nobody asked for in a month ---


def _age(path, days):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_prune_removes_thumbnails_unused_for_thirty_days():
    cache = gallery._cache_dir()
    cache.mkdir(parents=True)
    (cache / "old.webp").write_bytes(b"x")
    (cache / "tmpabc.tmp").write_bytes(b"x")  # left behind by a killed render
    (cache / "new.webp").write_bytes(b"x")
    (cache / "folder").mkdir()
    _age(cache / "old.webp", 31)
    _age(cache / "tmpabc.tmp", 31)
    _age(cache / "folder", 31)
    assert gallery.prune_thumbnails() == 2
    assert sorted(p.name for p in cache.iterdir()) == ["folder", "new.webp"]


def test_a_thumbnail_in_use_survives_the_prune(tmp_path):
    src = tmp_path / "a.png"
    _png(src)
    gallery.thumbnail(src, 32)
    (cached,) = gallery._cache_dir().iterdir()
    _age(cached, 40)
    gallery.thumbnail(src, 32)  # served from the cache: that counts as use
    assert gallery.prune_thumbnails() == 0
    assert cached.exists()


def test_prune_without_a_cache_is_a_no_op():
    assert gallery.prune_thumbnails() == 0


def test_prune_skips_what_it_cannot_remove(monkeypatch):
    cache = gallery._cache_dir()
    cache.mkdir(parents=True)
    (cache / "old.webp").write_bytes(b"x")
    _age(cache / "old.webp", 31)

    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(gallery.os, "unlink", refuse)
    assert gallery.prune_thumbnails() == 0


# --- scans read lazily and breadth first ---


def test_a_scan_stops_reading_at_its_cap(tmp_path, monkeypatch):
    # A folder with millions of entries must not be read into memory whole
    # before the cap applies.
    monkeypatch.setattr(gallery, "_IMAGE_SCAN_CAP", 5)

    class Entry:
        def __init__(self, index):
            self.name = f"{index}.txt"
            self.path = str(tmp_path / self.name)

        def is_dir(self, *, follow_symlinks=True):
            return False

        def is_file(self, *, follow_symlinks=True):
            return True

    def endless():
        for index in range(50):
            yield Entry(index)
        raise AssertionError("read past the scan cap")

    monkeypatch.setattr(gallery.os, "scandir", lambda _p: _Listing(endless()))
    assert gallery.scan_images(tmp_path).capped


def test_a_capped_scan_still_sees_every_shallow_folder(tmp_path, monkeypatch):
    # Depth first, the first deep subtree used the whole budget and a sibling
    # folder's images were never reached.
    monkeypatch.setattr(gallery, "_IMAGE_SCAN_CAP", 12)
    for side in ("x", "y"):
        _png(tmp_path / side / f"{side}.png")
        deep = tmp_path / side / "deep"
        deep.mkdir()
        for index in range(20):
            (deep / f"{index}.txt").write_text("x")
    scan = gallery.scan_images(tmp_path)
    assert scan.capped
    assert sorted(path.name for _, path in scan.images) == ["x.png", "y.png"]
