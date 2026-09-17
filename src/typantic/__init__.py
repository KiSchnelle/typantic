"""typantic — Auto-generate Typer CLI interfaces from Pydantic models."""

from importlib.metadata import version

from typantic._config_file import (
    build_config_template,
    load_config_file,
    write_config_template,
)
from typantic._decorator import add_command, pydantic_to_typer
from typantic._main import make_main

__version__ = version("typantic")
__all__ = [
    "add_command",
    "build_config_template",
    "load_config_file",
    "make_main",
    "pydantic_to_typer",
    "write_config_template",
]
