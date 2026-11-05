import pytest

from typantic.web._files import read_json_mapping


def test_read_json_mapping_returns_the_object(tmp_path):
    path = tmp_path / "ok.json"
    path.write_bytes('{"a": "Gr\u00f6\u00dfe"}'.encode())
    assert read_json_mapping(path) == {"a": "Gr\u00f6\u00dfe"}


def test_read_json_mapping_on_a_missing_file(tmp_path):
    assert read_json_mapping(tmp_path / "nope.json") is None


@pytest.mark.parametrize(
    "raw",
    [b"{not json", b"[1, 2]", b'{"a": "\xff"}', b"[" * 100000],
    ids=["not-json", "not-an-object", "invalid-bytes", "too-deep"],
)
def test_read_json_mapping_on_a_corrupt_file(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    assert read_json_mapping(path) is None
