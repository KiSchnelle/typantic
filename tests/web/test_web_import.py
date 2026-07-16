import builtins
import importlib
import sys

import pytest


def test_web_import_requires_extra(monkeypatch):
    # Drop cached web modules so the reimport re-runs typantic/web/__init__.py.
    for name in list(sys.modules):
        if name == "typantic.web" or name.startswith("typantic.web."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            msg = "No module named 'fastapi'"
            raise ModuleNotFoundError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModuleNotFoundError, match=r"typantic\[web\]"):
        importlib.import_module("typantic.web")
