# typantic

[![CI](https://github.com/KiSchnelle/typantic/actions/workflows/ci.yml/badge.svg)](https://github.com/KiSchnelle/typantic/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/typantic.svg)](https://pypi.org/project/typantic/)
[![Python](https://img.shields.io/pypi/pyversions/typantic.svg)](https://pypi.org/project/typantic/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Auto-generate [Typer](https://typer.tiangolo.com/) CLI interfaces from [Pydantic](https://docs.pydantic.dev/) models.

Define your config **once** as a Pydantic model with validators, and get a
fully-typed CLI for free — no duplication, no drift.

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
│    --seed       INTEGER  Random seed.                        │
│    --help                Show this message and exit.         │
╰──────────────────────────────────────────────────────────────╯
```

## How it works

The `@pydantic_to_typer(Model)` decorator:

1. Reads `Model.model_fields` to discover field names, types, descriptions, and defaults
2. Strips `Annotated` validator metadata to extract the base types Typer understands
3. Maps `kw_only=False` → `typer.Argument`, `kw_only=True` → `typer.Option`
4. Rewrites the function's `__signature__` so Typer sees the expanded parameters
5. At call time, passes the raw CLI values into `Model(...)` so all Pydantic validators run

Your function receives the **validated model instance** — validators, `default_factory`, union types, and everything else works exactly as in Pydantic.

## Features

| Pydantic                          | CLI result                              |
|-----------------------------------|-----------------------------------------|
| `kw_only=False`                   | `typer.Argument` (positional)           |
| `kw_only=True` or unset           | `typer.Option` (`--flag`)               |
| `Field(description=...)`          | `help=...` in the CLI                   |
| `Field(default=...)`              | Default value shown in `--help`         |
| `Field(default_factory=...)`      | Factory called once at decoration time  |
| `int \| None`                     | Optional CLI option                     |
| `list[Path]`                      | Variadic positional argument            |
| `AfterValidator`, `BeforeValidator` | Run at call time via Pydantic         |

## Requirements

- Python ≥ 3.12
- Pydantic ≥ 2.0
- Typer ≥ 0.26

## License

MIT
