# typantic-demo

A tiny, runnable example of a `typantic[web]` app. Its two settings models become
both a Typer CLI and a web form.

- **Detect objects** — streams a leveled log and writes colourful overlay PNGs
  (so the dashboard's log tail and image gallery have real output to show).
- **Train a model** — streams a per-epoch loss to the log.

Every field has a default, so a job launches with one click.

## Run it

```bash
# from the typantic repo root
pip install ./examples/typantic_demo 'typantic[web]'

# as a CLI
typantic-demo detect --images 12 --preset accurate

# or in the web dashboard (discovers both commands automatically)
typantic web serve
```

The commands register under the `typantic.web_commands` entry-point group (see
`pyproject.toml`), which is all it takes for `typantic web serve` to find them —
no central file to edit.
