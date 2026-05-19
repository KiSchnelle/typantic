"""typantic — Auto-generate Typer CLI interfaces from Pydantic models."""

from importlib.metadata import version

from typantic._decorator import pydantic_to_typer

__version__ = version("typantic")
__all__ = ["pydantic_to_typer"]
