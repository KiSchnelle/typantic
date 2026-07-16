"""The base ``typantic`` console entry point.

Provides ``typantic --version``. The ``typantic web`` subcommands are mounted
here once the optional ``[web]`` extra is installed (see ``typantic.web.cli``);
without the extra, invoking them prints an install hint.
"""

from importlib.metadata import version
from typing import Annotated

import typer


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(version("typantic"))
        raise typer.Exit


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Pydantic-driven CLI and web interfaces.",
)


@app.callback()
def _root(
    *,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed typantic version and exit.",
        ),
    ] = False,
) -> None:
    """Pydantic-driven CLI and web interfaces."""


@app.command(
    "web",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def web(ctx: typer.Context) -> None:
    """Run the web launcher/dashboard (requires the [web] extra)."""
    try:
        from typantic.web.cli import app as web_app  # noqa: PLC0415
    except ModuleNotFoundError:
        typer.echo(
            "The web interface requires the [web] extra: "
            "pip install 'typantic[web]'",
            err=True,
        )
        raise typer.Exit(1) from None
    web_app(args=ctx.args, prog_name="typantic web")


def main() -> None:
    """Run the ``typantic`` console script."""
    app()
