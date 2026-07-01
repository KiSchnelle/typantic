# typantic

[![CI](https://github.com/KiSchnelle/typantic/actions/workflows/ci.yml/badge.svg)](https://github.com/KiSchnelle/typantic/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/typantic.svg)](https://pypi.org/project/typantic/)
[![Python](https://img.shields.io/pypi/pyversions/typantic.svg)](https://pypi.org/project/typantic/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Auto-generate [Typer](https://typer.tiangolo.com/) CLI interfaces from [Pydantic](https://docs.pydantic.dev/) models — a **Pydantic → Typer** bridge.

Define your config **once** as a Pydantic model with validators and get a typed,
validated command-line interface for free — no duplication, no drift — plus
optional YAML/JSON config files (`--config` / `--generate-config`).

## Installation

```bash
pip install typantic
```

## Quick start

```python
from pathlib import Path
from typing import Annotated

import typer
from pydantic import AfterValidator, BaseModel, Field

from typantic import pydantic_to_typer


# 1. Define your config with validators
class Config(BaseModel):
    images: Annotated[
        list[Path],
        Field(description="Image folders to process.", kw_only=False),
    ]
    output: Annotated[
        Path,
        AfterValidator(Path.resolve),
        Field(description="Output directory.", kw_only=True),
    ]
    threshold: Annotated[
        float,
        Field(default=0.5, description="Detection threshold.", kw_only=True),
    ]
    seed: Annotated[
        int | None,
        Field(default=None, description="Random seed.", kw_only=True),
    ]


# 2. Use the decorator — that's it
app = typer.Typer()

@app.command()
@pydantic_to_typer(Config)
def run(config: Config):
    """Process images with validation."""
    print(config)

if __name__ == "__main__":
    app()
```

```
$ python example.py --help

 Usage: example.py [OPTIONS] IMAGES...

 Process images with validation.

╭─ Arguments ──────────────────────────────────────────────────╮
│ *  images  IMAGES...  Image folders to process.  [required]  │
╰──────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────╮
│ *  --output     PATH     Output directory.  [required]       │
│    --threshold  FLOAT    Detection threshold.  [default: 0.5]│
│    --seed       INTEGER  Random seed.  [default: (None)]     │
│    --help                Show this message and exit.         │
╰──────────────────────────────────────────────────────────────╯
```

## How it works

The `@pydantic_to_typer(Model)` decorator:

1. Reads `Model.model_fields` to discover field names, types, descriptions, and defaults
2. Strips `Annotated` validator metadata to extract the base types Typer understands
3. Maps `kw_only=False` → `typer.Argument`, `kw_only=True` → `typer.Option`
4. Flattens nested `BaseModel` fields into prefixed parameters
5. Rewrites the function's `__signature__` so Typer sees the expanded parameters
6. At call time, re-nests the raw CLI values and passes them into `Model(...)` so all Pydantic validators run

Your function receives the **validated model instance** — validators, `default_factory`, union types, and everything else works exactly as in Pydantic.

## Features

| Pydantic                          | CLI result                              |
|-----------------------------------|-----------------------------------------|
| `kw_only=False`                   | `typer.Argument` (positional)           |
| `kw_only=True` or unset           | `typer.Option` (`--flag`)               |
| `Field(description=...)`          | `help=...` in the CLI                   |
| `Field(default=...)`              | Default value shown in `--help`         |
| `Field(default_factory=...)`      | Re-evaluated per invocation; `--help` shows `[default: (computed at runtime)]` |
| `Field(ge=..., le=...)`           | Typer `min` / `max` (validated + shown) |
| `Literal["a", "b"]`               | CLI choices                             |
| `Enum`, `tuple[...]`              | Choices / multi-value option            |
| nested `BaseModel`                | Flattened into `--prefix-field` options |
| `SecretStr`, `SecretBytes`        | Hidden input (secure prompt if required)|
| `int \| None`                     | Optional CLI option                     |
| `default=None`                    | Rendered as `[default: (None)]`         |
| `list[Path]`                      | Variadic positional argument            |
| `AfterValidator`, `BeforeValidator` | Run at call time via Pydantic         |

Validators that raise `ValueError` / `AssertionError` surface as Typer
parameter errors; other exception types propagate unchanged.

## Per-field CLI hints

Customise individual flags with `Field(json_schema_extra=...)`:

```python
class Config(BaseModel):
    verbose: Annotated[
        bool,
        Field(default=False, json_schema_extra={"cli_short": "-v"}),
    ]
    output: Annotated[
        Path,
        Field(description="Output path.", json_schema_extra={"cli_name": "--dest"}),
    ]
    api_key: Annotated[
        str,
        Field(default="", json_schema_extra={"cli_envvar": "MYAPP_API_KEY"}),
    ]
```

| Key          | Effect                                              |
|--------------|-----------------------------------------------------|
| `cli_short`  | Adds a short flag (e.g. `-v`) alongside the long one |
| `cli_name`   | Replaces the derived long flag (e.g. `--dest`)      |
| `cli_envvar` | Reads the value from an environment variable        |

## Nested models

Fields whose type is itself a `BaseModel` are flattened into prefixed options,
so layered configs map onto the CLI without manual wiring:

```python
class Database(BaseModel):
    host: Annotated[str, Field(default="localhost", description="DB host.")]
    port: Annotated[int, Field(default=5432, ge=1, le=65535, description="DB port.")]


class Config(BaseModel):
    name: Annotated[str, Field(description="App name.", kw_only=False)]
    db: Database  # -> --db-host, --db-port
```

```
$ python example.py myapp --db-host db.internal --db-port 9000
```

The values are re-nested before the model is constructed, so `Database`'s own
validators and defaults apply as usual.

## Registering commands without a stub

`add_command` wires a model and a handler onto a Typer app directly, skipping
the decorate-a-stub-function boilerplate:

```python
import typer

from typantic import add_command

app = typer.Typer()


def run(config: Config) -> None:
    print(config)


add_command(app, Config, run)            # command name defaults to "run"
add_command(app, Config, run, name="go")  # or set it explicitly
```

## Help panels for mixin-composed models

Large configs composed from mixins can group their options into titled Rich
help panels. Opt in with `subpanels=True` and give each mixin a `cli_panel`
class attribute — every option lands in the panel of the class that defines
its field:

```python
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field

from typantic import pydantic_to_typer


class ComputeMixin(BaseModel):
    cli_panel: ClassVar[str] = "Compute"

    cpus: Annotated[int, Field(default=4, description="CPU count.")]


class Config(ComputeMixin):
    dry_run: Annotated[bool, Field(default=False, description="Dry run.")]


@app.command()
@pydantic_to_typer(Config, subpanels=True)
def run(config: Config): ...
```

```
$ python example.py --help

 Usage: example.py [OPTIONS]

╭─ Options ──────────────────────────────────────────────────────╮
│ --dry-run    --no-dry-run    Dry run.  [default: no-dry-run]   │
│ --help                       Show this message and exit.       │
╰────────────────────────────────────────────────────────────────╯
╭─ Compute ──────────────────────────────────────────────────────╮
│ --cpus        INTEGER        CPU count.  [default: 4]          │
╰────────────────────────────────────────────────────────────────╯
```

`--cpus` renders under a "Compute" panel; `--dry-run` stays in the default
options group (its defining class declares no `cli_panel`). Arguments are
never panelled.

## Config files

Some configs are too large or too nested to pass as flags every time. Opt in with
`config_file=True` and the command can be driven by a YAML/JSON file as well. Two
options are injected:

- `--generate-config PATH` — write an editable default template, then exit without
  running;
- `--config PATH` — load settings from a file as the base; any flags you also pass
  **override** the file.

```python
from typing import Annotated

import typer
from pydantic import BaseModel, Field

from typantic import add_command


class Database(BaseModel):
    host: Annotated[str, Field(description="DB host.")]      # required
    port: Annotated[int, Field(default=5432, description="DB port.")]


class Config(BaseModel):
    name: Annotated[str, Field(description="App name.")]      # required
    db: Database                                             # required nested model
    workers: Annotated[int, Field(default=4, description="Worker count.")]
    tags: set[str] = {"default"}


app = typer.Typer()


def run(config: Config) -> None:
    print(config)


add_command(app, Config, run, name="run", config_file=True)
```

Generate a template — required fields become `<REQUIRED: ...>` placeholders,
nested models are expanded so their shape is visible, and any
`default_factory` field becomes a `<DEFAULT: computed at runtime>` sentinel
(rather than a frozen value) so it is recomputed fresh when the file is loaded —
handy for host/time-sensitive defaults like a timestamped output folder or a CPU
count that shouldn't be baked into a shared template:

```console
$ myapp run --generate-config run.yaml
$ cat run.yaml
name: '<REQUIRED: App name.>'
db:
  host: '<REQUIRED: DB host.>'
  port: 5432
workers: 4
tags:
- default
```

Fill in the required values and run from the file (or override individual
settings with flags, which take precedence over the file):

```console
$ cat run.yaml
name: my-service
db:
  host: db.internal
  port: 9000
workers: 8
tags: [eu, prod]

$ myapp run --config run.yaml                 # run entirely from the file
$ myapp run --config run.yaml --workers 16    # file as base, --workers overrides
```

`--help` lists both options under a **Config file** panel:

```
╭─ Config file ──────────────────────────────────────────────────╮
│ --config           PATH  Load settings from a YAML/JSON file    │
│                          (flags passed still override).         │
│ --generate-config  PATH  Write a default config template to     │
│                          PATH and exit.                         │
╰────────────────────────────────────────────────────────────────╯
```

Because `--config` may supply them, required fields are made optional at the Typer
layer; Pydantic re-checks requiredness *after* merging file and flags, so a value
missing from both is still reported as an error — it just no longer renders as
`[required]` in `--help`. A `--config` document must be a mapping; a bad suffix,
unparseable content, or a non-mapping top level raises a `ValueError`.

### File-only commands

Some models can't map onto flat flags at all — nested-model lists, or
`scalar | (min, max)` ranges. For those, pass `config_file="only"`: the command
exposes **just** `--config` / `--generate-config`, with no per-field flags.

```python
add_command(app, TuneConfig, run, config_file="only", help="Tune from a config file.")
```

## Requirements

- Python ≥ 3.12 (tested on 3.12–3.15)
- Pydantic ≥ 2.10
- Typer ≥ 0.26
- PyYAML ≥ 6.0

## License

MIT
