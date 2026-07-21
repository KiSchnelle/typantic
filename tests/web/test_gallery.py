import json
import os
from datetime import UTC, datetime
from pathlib import Path

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
    found = gallery.scan_images(tmp_path)
    assert [p.name for p in found] == ["new.png", "old.png"]


def test_scan_images_missing_root(tmp_path):
    assert gallery.scan_images(tmp_path / "nope") == []


def test_scan_images_depth_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_MAX_DEPTH", 0)
    _png(tmp_path / "top.png")
    _png(tmp_path / "deep" / "img.png")
    found = gallery.scan_images(tmp_path)
    assert [p.name for p in found] == ["top.png"]


def test_scan_images_scan_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_SCAN_CAP", 1)
    _png(tmp_path / "a.png")
    _png(tmp_path / "b.png")
    _png(tmp_path / "c.png")
    assert len(gallery.scan_images(tmp_path)) <= 1


def test_scan_images_entry_error_is_skipped(tmp_path, monkeypatch):
    class BadEntry:
        path = str(tmp_path / "x.png")

        def is_dir(self, *, follow_symlinks=True):
            raise OSError

    monkeypatch.setattr(gallery.os, "scandir", lambda _p: [BadEntry()])
    assert gallery.scan_images(tmp_path) == []


# --- list_images ---


def test_list_images(tmp_path):
    _png(tmp_path / "a.png", mtime=100)
    record = _record(tmp_path)
    images = gallery.list_images(record, "job1")
    assert images[0].name == "a.png"
    assert images[0].root == 0
    assert "job1/image?root=0&path=a.png" in images[0].url


def test_list_images_skips_non_dir_root(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    ghost = tmp_path / "ghost"  # external, absolute, never created -> a non-dir root
    _png(job / "a.png")
    record = _record(job, {"output_folder": str(ghost)})
    images = gallery.list_images(record, "job1")
    assert [img.root for img in images] == [0]


def test_list_images_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "_IMAGE_LIMIT", 1)
    _png(tmp_path / "a.png", mtime=100)
    _png(tmp_path / "b.png", mtime=200)
    assert len(gallery.list_images(_record(tmp_path), "j")) == 1


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
