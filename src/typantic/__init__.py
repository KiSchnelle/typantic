"""typantic — Auto-generate Typer CLI interfaces from Pydantic models."""

import importlib.metadata

from typantic._config_file import (
    build_config_template,
    load_config_file,
    write_config_template,
)
from typantic._decorator import add_command, pydantic_to_typer
from typantic._main import make_main

try:
    __version__ = importlib.metadata.version("typantic")
except importlib.metadata.PackageNotFoundError:
    # A frozen app or a bare source tree has no installed metadata; importing
    # typantic must not fail over a version string.
    __version__ = "0+unknown"
__all__ = [
    "add_command",
    "build_config_template",
    "load_config_file",
    "make_main",
    "pydantic_to_typer",
    "write_config_template",
]
