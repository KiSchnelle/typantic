import json
import os
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

    monkeypatch.setattr(gallery.os, "scandir", lambda _p: iter([BadEntry()]))
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
    monkeypatch.setattr(gallery, "_THUMB_CACHE", tmp_path / "cache")
    source = tmp_path / "a.png"
    _png(source)
    first = gallery.thumbnail(source, 32)
    assert first is not None
    assert first.suffix == ".webp"
    assert first.is_file()
    second = gallery.thumbnail(source, 32)
    assert second == first  # cache hit


def test_thumbnail_corrupt_image(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_THUMB_CACHE", tmp_path / "cache")
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    assert gallery.thumbnail(bad, 32) is None


def test_thumbnail_missing_source(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_THUMB_CACHE", tmp_path / "cache")
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
    assert Image.open(thumb).convert("RGB").getpixel((0, 0)) == (255, 255, 255)


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
    w, h = Image.open(thumb).size
    assert h > w  # transposed; without exif_transpose this would still be wide


def test_tests_never_touch_the_real_thumbnail_cache():
    real = Path.home() / ".cache" / "typantic" / "thumbnails"
    assert real != gallery._THUMB_CACHE


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
        return [Named(e, "caf\udce9.png") if e.name == "b.png" else e for e in entries]

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
    with Image.open(thumb) as img:
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
    first = gallery.thumbnail(src, 32)
    monkeypatch.setattr(gallery, "_THUMB_VERSION", "next")
    assert gallery.thumbnail(src, 32) != first
