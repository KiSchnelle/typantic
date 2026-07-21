"""The ``typantic web`` Typer app: one ``serve`` command that starts the dashboard."""

import getpass
import logging
from pathlib import Path
from typing import Annotated

import typer

from typantic.web.launcher import Launcher
from typantic.web.server import (
    find_free_port,
    local_server_name,
    resolve_token,
    serve,
    startup_banner,
)
from typantic.web.store import JobStore

logger = logging.getLogger("typantic.web")

app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="A per-user job launcher + dashboard for typantic CLI apps.",
)


@app.callback()
def _root() -> None:
    """Keep ``serve`` an explicit subcommand (room for more commands later)."""


@app.command("serve")
def serve_command(
    host: Annotated[
        str,
        typer.Option(help="Interface to bind (localhost by default)."),
    ] = "127.0.0.1",
    port: Annotated[
        int | None,
        typer.Option(help="Port to bind; a free ephemeral port is picked if unset."),
    ] = None,
    jobs_dir: Annotated[
        Path | None,
        typer.Option(help="Job store root (default: ~/.typantic/jobs)."),
    ] = None,
    token: Annotated[
        str | None,
        typer.Option(help="Auth token; a random one is generated if unset."),
    ] = None,
    no_token: Annotated[
        bool,
        typer.Option("--no-token", help="Disable auth (localhost dev only)."),
    ] = False,
    title: Annotated[
        str,
        typer.Option(help="Dashboard brand shown in the UI."),
    ] = "typantic web",
    log_level: Annotated[
        str,
        typer.Option(
            help="Uvicorn log level: critical, error, warning, info, debug, or trace.",
        ),
    ] = "info",
) -> None:
    """Start the dashboard, printing the tokenized localhost URL to open."""
    launcher = Launcher(JobStore(jobs_dir))
    if not launcher.commands:
        logger.warning(
            "No commands discovered — install apps that register under the "
            "'typantic.web_commands' entry-point group so their commands appear.",
        )
    resolved_token = resolve_token(token, disable=no_token)
    resolved_port = port or find_free_port(host)

    for line in startup_banner(
        title=title,
        host=host,
        port=resolved_port,
        token=resolved_token,
        user=getpass.getuser(),
        server=local_server_name(),
    ):
        typer.echo(line)

    serve(
        launcher,
        host=host,
        port=resolved_port,
        token=resolved_token,
        title=title,
        log_level=log_level,
    )
