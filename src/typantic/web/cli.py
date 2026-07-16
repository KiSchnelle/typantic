"""The ``typantic web`` Typer app: one ``serve`` command that starts the dashboard."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from typantic.web.launcher import Launcher
from typantic.web.server import dashboard_url, find_free_port, resolve_token, serve
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
    url = dashboard_url(host, resolved_port, resolved_token)

    typer.echo("")
    typer.echo(f"  {title} is running. Open:")
    typer.echo(f"    {url}")
    if resolved_token:
        typer.echo("  (the token in the URL is the credential; keep it private)")
    typer.echo("  Remote host? Forward it with:")
    typer.echo(f"    ssh -N -L {resolved_port}:{host}:{resolved_port} <this-host>")
    typer.echo("")

    serve(launcher, host=host, port=resolved_port, token=resolved_token, title=title)
