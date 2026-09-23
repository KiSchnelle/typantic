import pytest

from typantic.web import gallery


@pytest.fixture(autouse=True)
def _isolated_thumbnail_cache(tmp_path, monkeypatch):
    # Rendering a thumbnail writes into the cache; without this every test that
    # renders one (directly or through the image route) writes into the
    # developer's real ~/.cache/typantic/thumbnails.
    monkeypatch.setattr(gallery, "_THUMB_CACHE", tmp_path / "thumbnail-cache")
