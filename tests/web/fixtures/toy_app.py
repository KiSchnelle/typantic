"""A tiny Typer app built with typantic, used as a real `--schema` source in tests.

Run as: `python toy_app.py run --schema` -> prints the settings model's JSON
Schema (the contract the web launcher subprocesses).
"""

from typing import Annotated

import typer
from pydantic import BaseModel, Field

from typantic import add_command


class ToyConfig(BaseModel):
    name: Annotated[str, Field(description="A required name.")]
    seed: Annotated[int | None, Field(default=None, description="An optional seed.")]
    workers: Annotated[int, Field(default=4, description="Worker count.")]


app = typer.Typer()


def _run(config: ToyConfig) -> None:
    typer.echo(config.model_dump_json())


add_command(app, ToyConfig, _run, name="run", config_file=True)
add_command(app, ToyConfig, _run, name="other", config_file=True)


if __name__ == "__main__":
    app()
