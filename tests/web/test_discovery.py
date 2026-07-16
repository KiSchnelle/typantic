import types

from typantic.web import discovery as disc


def _entry(name, loader):
    return types.SimpleNamespace(name=name, load=loader)


def _cmd(app, command):
    return {"app": app, "command": command, "argv": [command], "title": command}


def _patch_entries(monkeypatch, entries):
    def fake(*, group):
        assert group == "typantic.web_commands"
        return entries

    monkeypatch.setattr(disc, "entry_points", fake)


def test_discover_sorted_by_app_then_command(monkeypatch):
    entries = [
        _entry("b", lambda: [_cmd("bapp", "z"), _cmd("bapp", "a")]),
        _entry("a", lambda: [_cmd("aapp", "run")]),
    ]
    _patch_entries(monkeypatch, entries)
    keys = [m.key for m in disc.discover_commands()]
    assert keys == ["aapp/run", "bapp/a", "bapp/z"]


def test_discover_isolates_a_broken_entry(monkeypatch):
    def boom():
        raise RuntimeError("broken app")

    entries = [
        _entry("broken", boom),
        _entry("good", lambda: [_cmd("goodapp", "run")]),
    ]
    _patch_entries(monkeypatch, entries)
    keys = [m.key for m in disc.discover_commands()]
    assert keys == ["goodapp/run"]


def test_discover_skips_non_list_payload(monkeypatch):
    entries = [_entry("weird", lambda: {"not": "a list"})]
    _patch_entries(monkeypatch, entries)
    assert disc.discover_commands() == []


def test_discover_skips_malformed_command(monkeypatch):
    entries = [
        _entry("mix", lambda: [{"app": "x"}, _cmd("xapp", "run")]),  # first is invalid
    ]
    _patch_entries(monkeypatch, entries)
    keys = [m.key for m in disc.discover_commands()]
    assert keys == ["xapp/run"]


def test_command_catalog_groups_by_app(monkeypatch):
    entries = [
        _entry("e", lambda: [_cmd("app1", "a"), _cmd("app1", "b"), _cmd("app2", "c")]),
    ]
    _patch_entries(monkeypatch, entries)
    catalog = disc.command_catalog()
    assert set(catalog) == {"app1", "app2"}
    assert [m.command for m in catalog["app1"]] == ["a", "b"]
    assert [m.command for m in catalog["app2"]] == ["c"]
