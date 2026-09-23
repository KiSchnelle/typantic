import pytest


@pytest.fixture(autouse=True)
def _isolated_thumbnail_cache(tmp_path, monkeypatch):
    # Rendering a thumbnail writes into the cache, and the server prunes it at
    # start; without this every test that renders one (directly or through the
    # image route) or starts a server touches the developer's real cache.
    monkeypatch.setenv("TYPANTIC_WEB_CACHE_DIR", str(tmp_path / "typantic-cache"))
