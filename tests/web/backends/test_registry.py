import types

from typantic.web.backends import registry
from typantic.web.backends.local import LocalBackend

_BUILTINS = {"local", "ssh", "slurm", "pbs", "docker", "podman", "apptainer"}


def test_load_backends_includes_builtins():
    backends = registry.load_backends()
    assert set(backends) >= _BUILTINS
    for backend in backends.values():
        assert hasattr(backend, "launch")
        assert hasattr(backend, "poll")
        assert hasattr(backend, "cancel")


def test_load_backends_isolates_broken(monkeypatch):
    def boom():
        raise RuntimeError

    good = types.SimpleNamespace(name="good", load=lambda: LocalBackend)
    bad = types.SimpleNamespace(name="bad", load=lambda: boom)

    def fake(*, group):
        assert group == "typantic.web_backends"
        return [bad, good]

    monkeypatch.setattr(registry, "entry_points", fake)
    backends = registry.load_backends()
    assert "good" in backends
    assert "bad" not in backends


def test_load_backends_registers_custom(monkeypatch):
    class MyBackend:
        pass

    entry = types.SimpleNamespace(name="mine", load=lambda: MyBackend)
    monkeypatch.setattr(registry, "entry_points", lambda *, group: [entry])
    backends = registry.load_backends()
    assert isinstance(backends["mine"], MyBackend)
