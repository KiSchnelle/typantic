"""The ``typantic web`` Typer app: one ``serve`` command that starts the dashboard."""

import getpass
import logging
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError

from typantic.web.brand import discover_brand, resolve_brand
from typantic.web.launcher import Launcher
from typantic.web.models import Brand
from typantic.web.server import (
    find_free_port,
    is_loopback_host,
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
        str | None,
        typer.Option(
            help="The dashboard's name, in the sidebar and the browser tab "
            "(default: the installed brand's, else typantic web).",
        ),
    ] = None,
    icon: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="An SVG file: the sidebar mark and the browser tab's icon.",
        ),
    ] = None,
    accent: Annotated[
        str | None,
        typer.Option(help="The accent colour, #rrggbb (default: typantic's cyan)."),
    ] = None,
    log_level: Annotated[
        Literal["critical", "error", "warning", "info", "debug", "trace"],
        typer.Option(help="Uvicorn log level."),
    ] = "info",
) -> None:
    """Start the dashboard, printing the tokenized localhost URL to open."""
    brand = _brand(title, icon, accent)
    launcher = Launcher(JobStore(jobs_dir))
    if not launcher.commands:
        logger.warning(
            "No commands discovered — install apps that register under the "
            "'typantic.web_commands' entry-point group so their commands appear.",
        )
    resolved_token = resolve_token(token, disable=no_token)
    # Keyed on the resolved token, not the flag, so `--token ""` cannot slip past.
    if resolved_token is None and not is_loopback_host(host):
        msg = (
            f"--no-token disables authentication, so it is only allowed on a "
            f"loopback host; {host!r} is reachable from the network. Either drop "
            f"--no-token (a random one is generated for you) or bind 127.0.0.1 "
            f"and reach it over an SSH tunnel."
        )
        raise typer.BadParameter(msg)
    resolved_port = port or find_free_port(host)

    for line in startup_banner(
        title=brand.title,
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
        brand=brand,
        log_level=log_level,
    )


def _brand(title: str | None, icon: Path | None, accent: str | None) -> Brand:
    """The installed brand with this run's flags applied, or a usage error."""
    try:
        markup = icon.read_text(encoding="utf-8") if icon is not None else None
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot be read as UTF-8 text ({exc})."
        raise typer.BadParameter(msg, param_hint="--icon") from exc
    try:
        return resolve_brand(discover_brand(), title=title, icon=markup, accent=accent)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise typer.BadParameter(
            error["msg"], param_hint=f"--{error['loc'][0]}"
        ) from exc
